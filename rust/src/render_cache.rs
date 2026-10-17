//! Reuse one hayro render cache across render calls on one document.
//!
//! hayro's `RenderCache` borrows its immutable `Pdf` snapshot and shares its
//! maps through `Rc`, so it can neither live inside the `Send + Sync`
//! `_Document` nor cross a thread. Each thread instead keeps at most one cache
//! in thread-local storage, owned together with the snapshot it borrows by a
//! `self_cell`. A document reuses that cache only on the thread that created
//! it, only for the snapshot generation identified by its live token, and only
//! while the cache has served a bounded number of distinct pages.

use std::cell::{RefCell, RefMut};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Weak};

use hayro::RenderCache;
use hayro::hayro_syntax::Pdf;

/// Distinct pages whose fonts and glyph outlines may stay cached between calls.
///
/// One call still grows its cache exactly as a call-scoped cache would; a
/// cache that has served more pages is dropped when the call returns.
const MAX_RETAINED_CACHE_PAGES: usize = 64;

type RenderCacheFor<'a> = RenderCache<'a>;

self_cell::self_cell!(
    /// One immutable hayro snapshot and the render cache that borrows it.
    struct SnapshotCache {
        owner: Arc<Pdf>,

        #[not_covariant]
        dependent: RenderCacheFor,
    }
);

/// Identity of one cacheable snapshot generation.
struct Generation;

/// The calling thread's retained cache and the generation it belongs to.
struct Slot {
    generation: Weak<Generation>,
    pages: Vec<u32>,
    cache: SnapshotCache,
}

impl Slot {
    /// The slot's `Weak` keeps the generation allocation alive, so pointer
    /// identity cannot be confused with a later generation at the same address.
    fn belongs_to(&self, generation: &Arc<Generation>) -> bool {
        std::ptr::eq(self.generation.as_ptr(), Arc::as_ptr(generation))
    }

    /// Whether no document can use this slot again.
    fn is_orphaned(&self) -> bool {
        self.generation.strong_count() == 0
    }

    /// Record distinct pages; return false once the retention bound is exceeded.
    fn record_pages(&mut self, pages: &[u32]) -> bool {
        for &page in pages {
            if self.pages.contains(&page) {
                continue;
            }
            if self.pages.len() >= MAX_RETAINED_CACHE_PAGES || self.pages.try_reserve(1).is_err() {
                return false;
            }
            self.pages.push(page);
        }
        true
    }
}

/// Source of process-unique thread keys; zero never identifies a thread.
static NEXT_THREAD_KEY: AtomicU64 = AtomicU64::new(1);

thread_local! {
    static SLOT: RefCell<Option<Slot>> = const { RefCell::new(None) };
    static THREAD_KEY: u64 = NEXT_THREAD_KEY.fetch_add(1, Ordering::Relaxed);
}

/// Identify the calling thread without `thread::current`, which panics once
/// thread-local data is being destroyed; `None` disables reuse.
fn thread_key() -> Option<u64> {
    THREAD_KEY.try_with(|key| *key).ok()
}

/// Clear a borrowed slot unless its render completed within the page bound.
///
/// A panic inside hayro unwinds through this guard, so a cache that may have
/// been left half-updated is never reused.
struct SlotGuard<'s> {
    slot: RefMut<'s, Option<Slot>>,
    keep: bool,
}

impl Drop for SlotGuard<'_> {
    fn drop(&mut self) {
        if !self.keep {
            *self.slot = None;
        }
    }
}

/// Per-document handle to the creating thread's retained render cache.
pub(crate) struct DocumentRenderCache {
    generation: Arc<Generation>,
    home: Option<u64>,
}

impl DocumentRenderCache {
    /// Start an empty generation owned by the calling thread.
    pub(crate) fn new() -> Self {
        Self {
            generation: Arc::new(Generation),
            home: thread_key(),
        }
    }

    fn on_home_thread(&self) -> bool {
        self.home.is_some() && thread_key() == self.home
    }

    /// End the current generation, releasing this thread's cache for it.
    ///
    /// Call whenever the snapshot or font resolution changes. A cache held by
    /// another thread can no longer match and is replaced or dropped there.
    pub(crate) fn reset(&mut self) {
        self.release_local();
        self.generation = Arc::new(Generation);
    }

    /// Render with a cache for `pdf`, retained across calls on the home thread.
    ///
    /// `pdf` must be the current snapshot of this generation and `pages` the
    /// one-based pages this call renders. Other threads, and a slot that is
    /// borrowed or being torn down, use a call-scoped cache exactly as before.
    pub(crate) fn with_cache<R>(
        &self,
        pdf: &Arc<Pdf>,
        pages: &[u32],
        render: impl for<'q, 'c> FnOnce(&'q Pdf, &'c RenderCache<'q>) -> R,
    ) -> R {
        let mut render = Some(render);
        if self.on_home_thread() {
            let retained = SLOT.try_with(|slot| {
                let mut guard = SlotGuard {
                    slot: slot.try_borrow_mut().ok()?,
                    keep: false,
                };
                let reusable = guard.slot.as_ref().is_some_and(|current| {
                    current.belongs_to(&self.generation)
                        && Arc::ptr_eq(current.cache.borrow_owner(), pdf)
                });
                if !reusable {
                    // Release a stale cache before building the next one.
                    *guard.slot = None;
                    *guard.slot = Some(Slot {
                        generation: Arc::downgrade(&self.generation),
                        pages: Vec::new(),
                        cache: SnapshotCache::new(Arc::clone(pdf), |_| RenderCache::new()),
                    });
                }
                let current = guard
                    .slot
                    .as_mut()
                    .expect("slot was filled immediately before");
                let render = render.take().expect("render runs once");
                let result = current
                    .cache
                    .with_dependent(|owner, cache| render(owner, cache));
                guard.keep = current.record_pages(pages);
                Some(result)
            });
            if let Ok(Some(result)) = retained {
                return result;
            }
        }
        let render = render.take().expect("render runs once");
        let cache = RenderCache::new();
        render(pdf, &cache)
    }

    /// Drop this thread's slot when it holds the current or an orphaned generation.
    fn release_local(&self) {
        if !self.on_home_thread() {
            return;
        }
        let _ = SLOT.try_with(|slot| {
            if let Ok(mut slot) = slot.try_borrow_mut()
                && slot.as_ref().is_some_and(|current| {
                    current.belongs_to(&self.generation) || current.is_orphaned()
                })
            {
                *slot = None;
            }
        });
    }
}

impl Drop for DocumentRenderCache {
    fn drop(&mut self) {
        self.release_local();
    }
}
