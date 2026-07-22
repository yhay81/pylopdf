//! lopdf::Document の Python バインディング。
//!
//! 型変換とエラー変換のみを担う薄い層。使いやすい API は Python 側の
//! `pylopdf.Document` が提供する。

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;

use hayro::hayro_interpret::InterpreterSettings;
use hayro::hayro_interpret::font::{FallbackFontQuery, FontData, FontQuery};
use hayro::hayro_interpret::hayro_cmap::CidFamily;
use hayro::hayro_syntax::Pdf;
use hayro::vello_cpu::color::AlphaColor;
use hayro::{RenderCache, RenderSettings, render};
use lopdf::encryption::crypt_filters::{Aes256CryptFilter, CryptFilter};
use lopdf::encryption::{EncryptionState, EncryptionVersion, Permissions};
use lopdf::{
    Bookmark, Dictionary, Document, LoadOptions, Object, ObjectId, PdfMetadata, SaveOptions,
    decode_text_string, dictionary, text_string,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

// Python 側へ公開する例外。PdfError は ValueError のサブクラスなので後方互換。
pyo3::create_exception!(
    pylopdf,
    PdfError,
    PyValueError,
    "pylopdf の基底例外（ValueError 互換）。"
);
pyo3::create_exception!(
    pylopdf,
    PasswordError,
    PdfError,
    "パスワードが必要か、正しくない。"
);

/// ページ辞書へ親ツリーから継承され得る属性キー。
const INHERITABLE_PAGE_KEYS: [&[u8]; 4] = [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

/// PNG レンダリングで許容する総画素数（RGBA bitmap 約 256 MB 相当）。
const MAX_RENDER_PIXELS: u64 = 64_000_000;

/// 文脈プレフィックス付きで lopdf のエラーを Python 例外に変換する。
///
/// パスワード起因（復号失敗・パスワード不一致）は PasswordError、それ以外は PdfError。
fn lopdf_err(prefix: Option<&str>, e: &lopdf::Error) -> PyErr {
    let message = match prefix {
        Some(p) => format!("{p}: {e}"),
        None => e.to_string(),
    };
    if matches!(
        e,
        lopdf::Error::Decryption(_) | lopdf::Error::InvalidPassword
    ) {
        PasswordError::new_err(message)
    } else {
        PdfError::new_err(message)
    }
}

/// lopdf のエラーを Python 例外に変換する。
fn to_py_err(e: lopdf::Error) -> PyErr {
    lopdf_err(None, &e)
}

/// PdfMetadata を Python へ渡すタプル（Info 文字列辞書, ページ数, バージョン, 暗号化有無）に変換する。
fn pdf_metadata_to_tuple(meta: PdfMetadata) -> (BTreeMap<String, String>, u32, String, bool) {
    let mut map = BTreeMap::new();
    let pairs = [
        ("Title", meta.title),
        ("Author", meta.author),
        ("Subject", meta.subject),
        ("Keywords", meta.keywords),
        ("Creator", meta.creator),
        ("Producer", meta.producer),
        ("CreationDate", meta.creation_date),
        ("ModDate", meta.modification_date),
    ];
    for (key, value) in pairs {
        if let Some(v) = value {
            map.insert(key.to_string(), v);
        }
    }
    (map, meta.page_count, meta.version, meta.encrypted)
}

/// object stream + xref stream を有効にした保存オプション。
///
/// ObjectStreamConfig の既定（100 obj / 圧縮レベル 6）をそのまま使う。
fn modern_save_options() -> SaveOptions {
    SaveOptions {
        use_object_streams: true,
        use_xref_streams: true,
        ..Default::default()
    }
}

/// ページ辞書に、親ツリーから継承される属性を焼き込んで返す。
///
/// merge では取り込み元のページツリー（親ノード）を捨てるため、
/// 継承頼みの属性をページ自身へ移しておく必要がある。
fn resolve_inherited_page_dict(doc: &Document, page_id: ObjectId) -> lopdf::Result<Dictionary> {
    let mut dict = doc.get_object(page_id)?.as_dict()?.clone();
    for key in INHERITABLE_PAGE_KEYS {
        if dict.has(key) {
            continue;
        }
        let mut parent = dict.get(b"Parent").and_then(Object::as_reference).ok();
        let mut visited = HashSet::from([page_id]);
        while let Some(parent_id) = parent {
            if !visited.insert(parent_id) {
                return Err(lopdf::Error::ReferenceCycle(parent_id));
            }
            let parent_dict = doc.get_object(parent_id)?.as_dict()?;
            if let Ok(value) = parent_dict.get(key) {
                dict.set(key, value.clone());
                break;
            }
            parent = parent_dict
                .get(b"Parent")
                .and_then(Object::as_reference)
                .ok();
        }
    }
    Ok(dict)
}

/// レンダリング時に非埋め込み CJK フォントへ充てる代替フォント。
#[derive(Default, Clone)]
struct FallbackFonts {
    /// ゴシック系（および判別不能時の既定）
    sans: Option<(Arc<Vec<u8>>, u32)>,
    /// 明朝系
    serif: Option<(Arc<Vec<u8>>, u32)>,
}

/// BaseFont 名の小文字表現に含まれていたら CJK フォントとみなすパターン。
const CJK_NAME_HINTS: [&str; 12] = [
    "mincho", "gothic", "ryumin", "kozmin", "kozgo", "kozuka", "meiryo", "yugoth", "yumin",
    "hiragino", "ipaex", "ipam",
];

/// BaseFont 名の小文字表現に含まれていたら明朝系とみなすパターン。
const SERIF_NAME_HINTS: [&str; 5] = ["mincho", "ryumin", "kozmin", "yumin", "serif"];

/// 非埋め込みフォントの問い合わせが CJK なら、設定済みの代替フォントを返す。
///
/// CIDSystemInfo（Adobe-Japan1/GB1/CNS1/Korea1）か BaseFont 名で CJK と判定する。
/// Adobe-Identity は CID→Unicode の手がかりが CMap に無いため名前判定に任せる
/// （埋め込み ToUnicode があれば hayro 側がそれを使って解決する）。
fn pick_cjk_fallback(fonts: &FallbackFonts, query: &FallbackFontQuery) -> Option<(FontData, u32)> {
    let is_cjk_collection = matches!(
        query.character_collection.as_ref().map(|cc| &cc.family),
        Some(
            CidFamily::AdobeJapan1
                | CidFamily::AdobeGB1
                | CidFamily::AdobeCNS1
                | CidFamily::AdobeKorea1
        )
    );
    let name = query
        .post_script_name
        .as_deref()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let is_cjk_name = CJK_NAME_HINTS.iter().any(|hint| name.contains(hint));
    if !is_cjk_collection && !is_cjk_name {
        return None;
    }
    let prefers_serif = SERIF_NAME_HINTS.iter().any(|hint| name.contains(hint));
    let slot = if prefers_serif {
        fonts.serif.as_ref().or(fonts.sans.as_ref())
    } else {
        fonts.sans.as_ref().or(fonts.serif.as_ref())
    };
    slot.map(|(data, index)| (Arc::clone(data) as FontData, *index))
}

/// lopdf::Document を保持する Python クラス。
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Document {
    /// 編集対象の本体（lopdf）。
    doc: Document,
    /// レンダリング時の CJK 代替フォント設定。
    fallback_fonts: FallbackFonts,
    /// レンダリング用にパース済みの hayro ドキュメント（現在の編集状態のスナップショット）。
    /// 編集メソッドが `invalidate_hayro_pdf` で破棄し、次のレンダリングで再構築される。
    hayro_pdf: Option<Pdf>,
}

impl _Document {
    /// lopdf::Document から（fallback フォント未設定の状態で）構築する。
    fn from_doc(doc: Document) -> Self {
        Self {
            doc,
            fallback_fonts: FallbackFonts::default(),
            hayro_pdf: None,
        }
    }

    /// trailer の Info 辞書を（間接参照を解決して）返す。
    fn info_dict(&self) -> Option<&Dictionary> {
        match self.doc.trailer.get(b"Info").ok()? {
            Object::Reference(id) => self.doc.get_object(*id).ok()?.as_dict().ok(),
            Object::Dictionary(dict) => Some(dict),
            _ => None,
        }
    }

    /// 現在の編集状態をシリアライズしたバイト列を返す（レンダリング用）。
    fn current_bytes(&mut self) -> PyResult<Vec<u8>> {
        let mut buffer = Vec::new();
        self.doc
            .save_to(&mut buffer)
            .map_err(|e| PdfError::new_err(e.to_string()))?;
        Ok(buffer)
    }

    /// キャッシュ済みのレンダリング用ビューを破棄する。編集メソッドの先頭で呼ぶ。
    fn invalidate_hayro_pdf(&mut self) {
        self.hayro_pdf = None;
    }

    /// 指定ページ（1 始まり）の ObjectId を返す。
    fn page_id(&self, page_number: u32) -> PyResult<ObjectId> {
        self.doc
            .get_pages()
            .get(&page_number)
            .copied()
            .ok_or_else(|| PdfError::new_err(format!("ページ {page_number} は存在しません")))
    }

    /// ページ辞書の属性を、親ツリーの継承と間接参照を解決しつつ取得する。
    fn resolve_page_attr(&self, page_id: ObjectId, key: &[u8]) -> PyResult<Option<Object>> {
        let mut current = Some(page_id);
        let mut visited = HashSet::new();
        while let Some(id) = current {
            if !visited.insert(id) {
                return Err(to_py_err(lopdf::Error::ReferenceCycle(id)));
            }
            let dict = self
                .doc
                .get_object(id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            if let Ok(value) = dict.get(key) {
                let resolved = match value {
                    Object::Reference(rid) => self.doc.get_object(*rid).map_err(to_py_err)?.clone(),
                    other => other.clone(),
                };
                return Ok(Some(resolved));
            }
            current = dict.get(b"Parent").and_then(Object::as_reference).ok();
        }
        Ok(None)
    }

    /// 現在の編集状態の hayro ドキュメントを返す（未構築ならシリアライズ + パースして保持）。
    ///
    /// 編集メソッドが `invalidate_hayro_pdf` でキャッシュを破棄するため、
    /// 「編集後の状態が常に反映される」不変条件は維持される。
    /// 連続レンダリングでは再構築が 1 回で済む。
    fn hayro_view(&mut self) -> PyResult<&Pdf> {
        if self.hayro_pdf.is_none() {
            let data = self.current_bytes()?;
            let pdf = Pdf::new(data).map_err(|e| {
                PdfError::new_err(format!("failed to parse PDF for rendering: {e:?}"))
            })?;
            self.hayro_pdf = Some(pdf);
        }
        Ok(self.hayro_pdf.as_ref().expect("直前で構築している"))
    }

    /// fallback フォント設定を反映した InterpreterSettings を組み立てる。
    fn interpreter_settings(&self) -> InterpreterSettings {
        let mut settings = InterpreterSettings::default();
        if self.fallback_fonts.sans.is_none() && self.fallback_fonts.serif.is_none() {
            return settings;
        }
        let fonts = self.fallback_fonts.clone();
        let default_resolver = settings.font_resolver.clone();
        settings.font_resolver = Arc::new(move |query| {
            if let FontQuery::Fallback(fallback) = query
                && let Some(picked) = pick_cjk_fallback(&fonts, fallback)
            {
                return Some(picked);
            }
            default_resolver(query)
        });
        settings
    }

    /// ルート Pages ノードの ObjectId を返す。無ければ最小構造を作る（空ドキュメント対応）。
    fn ensure_page_tree(&mut self) -> lopdf::Result<ObjectId> {
        let existing = self
            .doc
            .catalog()
            .and_then(|catalog| catalog.get(b"Pages"))
            .and_then(Object::as_reference);
        if let Ok(pages_id) = existing {
            return Ok(pages_id);
        }
        let pages_id = self.doc.add_object(dictionary! {
            "Type" => "Pages",
            "Kids" => Vec::<Object>::new(),
            "Count" => 0,
        });
        let catalog_id = self.doc.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        self.doc.trailer.set("Root", catalog_id);
        Ok(pages_id)
    }

    /// other の指定ページ（1 始まり・指定順）を self のオブジェクト空間へ取り込み、
    /// 取り込んだページの ObjectId 列を返す（root Kids への接続は呼び出し側が行う）。
    ///
    /// ページ辞書には継承属性を焼き込み、Parent を root Pages に付け替える。
    fn transplant_pages(
        &mut self,
        other: &Self,
        page_numbers: &[u32],
        pages_id: ObjectId,
    ) -> PyResult<Vec<ObjectId>> {
        let starting_id = self
            .doc
            .max_id
            .checked_add(1)
            .ok_or_else(|| PdfError::new_err("PDF オブジェクト ID が上限に達しています"))?;
        let mut other_doc = other.doc.clone();
        other_doc.renumber_objects_with(starting_id);
        let new_max_id = other_doc.max_id;

        let other_pages = other_doc.get_pages();
        let mut ordered_ids = Vec::with_capacity(page_numbers.len());
        for number in page_numbers {
            let id = *other_pages
                .get(number)
                .ok_or_else(|| PdfError::new_err(format!("ページ {number} は存在しません")))?;
            ordered_ids.push(id);
        }

        // 取り込み元のページツリーは捨てるため、継承属性を各ページへ焼き込む
        let mut resolved_pages = Vec::with_capacity(ordered_ids.len());
        for &page_id in &ordered_ids {
            let mut dict = resolve_inherited_page_dict(&other_doc, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            resolved_pages.push((page_id, dict));
        }

        // ページツリー構造（Catalog / Pages / Page）以外のオブジェクトを取り込む
        for (id, object) in other_doc.objects {
            match object.type_name().unwrap_or(b"") {
                b"Catalog" | b"Pages" | b"Page" => {}
                _ => {
                    self.doc.objects.insert(id, object);
                }
            }
        }
        for (id, dict) in resolved_pages {
            self.doc.objects.insert(id, Object::Dictionary(dict));
        }

        self.doc.max_id = new_max_id;
        Ok(ordered_ids)
    }

    /// root Pages の Kids/Count に new_ids を追記する（末尾追加の高速パス。平坦化しない）。
    fn append_pages(&mut self, pages_id: ObjectId, new_ids: Vec<ObjectId>) -> PyResult<()> {
        // 入力 PDF の Count は壊れていることがあるため、実際に到達可能なページ数から再計算する。
        // new_ids はまだ Kids から参照されていないので、ここでの get_pages は既存ページだけを返す。
        let total_count = self
            .doc
            .get_pages()
            .len()
            .checked_add(new_ids.len())
            .ok_or_else(|| PdfError::new_err("ページ数が上限に達しています"))?;
        let count = i64::try_from(total_count).map_err(|e| PdfError::new_err(e.to_string()))?;
        let pages_dict = self
            .doc
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let mut kids = match pages_dict.get(b"Kids").and_then(Object::as_array) {
            Ok(kids) => kids.clone(),
            Err(_) => Vec::new(),
        };
        kids.extend(new_ids.into_iter().map(Object::Reference));
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);
        Ok(())
    }

    /// 現在のページ列に new_ids を position（0 始まり、None で末尾）で挿入した並びを返す。
    ///
    /// new_ids はまだ root Kids から到達できないこと（get_pages に含まれないこと）が前提。
    fn spliced_page_order(&self, new_ids: Vec<ObjectId>, position: Option<usize>) -> Vec<ObjectId> {
        let mut order: Vec<ObjectId> = self.doc.get_pages().into_values().collect();
        let pos = position.unwrap_or(order.len()).min(order.len());
        order.splice(pos..pos, new_ids);
        order
    }

    /// AES-256（PDF 2.0, V5/R6）で暗号化した複製を作る。self は平文のまま変わらない。
    ///
    /// file_encryption_key は呼び出し側（Python の os.urandom）が生成した 32 バイトの乱数。
    fn encrypted_clone(
        &self,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<Document> {
        if file_encryption_key.len() != 32 {
            return Err(PdfError::new_err(format!(
                "file_encryption_key は 32 バイトである必要があります（{} バイト）",
                file_encryption_key.len()
            )));
        }
        let crypt_filter: Arc<dyn CryptFilter> = Arc::new(Aes256CryptFilter);
        let version = EncryptionVersion::V5 {
            encrypt_metadata: true,
            crypt_filters: BTreeMap::from([(b"StdCF".to_vec(), crypt_filter)]),
            file_encryption_key,
            stream_filter: b"StdCF".to_vec(),
            string_filter: b"StdCF".to_vec(),
            owner_password,
            user_password,
            permissions: Permissions::from_bits_truncate(permissions),
        };
        let state = EncryptionState::try_from(version).map_err(to_py_err)?;
        let mut cloned = self.doc.clone();
        cloned.encrypt(&state).map_err(to_py_err)?;
        Ok(cloned)
    }

    /// root Pages の Kids/Count を指定の並びで置き換える（ページツリーの平坦化）。
    ///
    /// 各ページには継承属性を焼き込み、Parent を root に付け替える。
    /// 旧中間ノードの掃除（prune_objects）は呼び出し側で行う。
    fn rebuild_page_tree(&mut self, pages_id: ObjectId, ordered: Vec<ObjectId>) -> PyResult<()> {
        for &page_id in &ordered {
            let mut dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            self.doc.objects.insert(page_id, Object::Dictionary(dict));
        }
        let kids: Vec<Object> = ordered.iter().map(|&id| Object::Reference(id)).collect();
        let count = i64::try_from(kids.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
        let pages_dict = self
            .doc
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);
        Ok(())
    }
}

#[pymethods]
impl _Document {
    /// 空の PDF ドキュメントを作る。
    #[new]
    fn new() -> PyResult<Self> {
        let mut document = Self::from_doc(Document::with_version("1.7"));
        document.ensure_page_tree().map_err(to_py_err)?;
        Ok(document)
    }

    /// ファイルパスから読み込む。
    ///
    /// password は暗号化 PDF の復号に使う。max_decompressed_size は
    /// 1 ストリームあたりの展開上限バイト数（解凍爆弾対策、None で無制限）。
    #[staticmethod]
    #[pyo3(signature = (path, password=None, max_decompressed_size=None))]
    fn load(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
        max_decompressed_size: Option<usize>,
    ) -> PyResult<Self> {
        let options = LoadOptions {
            password,
            max_decompressed_size,
            ..Default::default()
        };
        py.detach(|| {
            Document::load_with_options(path, options)
                .map(Self::from_doc)
                .map_err(|e| lopdf_err(Some(&format!("failed to load {path}")), &e))
        })
    }

    /// バイト列から読み込む。引数の意味は load と同じ。
    #[staticmethod]
    #[pyo3(signature = (data, password=None, max_decompressed_size=None))]
    fn load_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
        max_decompressed_size: Option<usize>,
    ) -> PyResult<Self> {
        let options = LoadOptions {
            password,
            max_decompressed_size,
            ..Default::default()
        };
        py.detach(|| {
            Document::load_mem_with_options(data, options)
                .map(Self::from_doc)
                .map_err(to_py_err)
        })
    }

    /// 文書全体をロードせず、メタデータだけを高速に読む。
    ///
    /// 戻り値は (Info 文字列辞書, ページ数, バージョン, 暗号化有無)。
    #[staticmethod]
    #[pyo3(signature = (path, password=None))]
    fn load_metadata(
        py: Python<'_>,
        path: &str,
        password: Option<String>,
    ) -> PyResult<(BTreeMap<String, String>, u32, String, bool)> {
        py.detach(|| {
            let meta = match &password {
                Some(pw) => Document::load_metadata_with_password(path, pw),
                None => Document::load_metadata(path),
            }
            .map_err(|e| lopdf_err(Some(&format!("failed to load {path}")), &e))?;
            Ok(pdf_metadata_to_tuple(meta))
        })
    }

    /// バイト列からメタデータだけを高速に読む。戻り値は load_metadata と同じ。
    #[staticmethod]
    #[pyo3(signature = (data, password=None))]
    fn load_metadata_bytes(
        py: Python<'_>,
        data: &[u8],
        password: Option<String>,
    ) -> PyResult<(BTreeMap<String, String>, u32, String, bool)> {
        py.detach(|| {
            let meta = match &password {
                Some(pw) => Document::load_metadata_mem_with_password(data, pw),
                None => Document::load_metadata_mem(data),
            }
            .map_err(to_py_err)?;
            Ok(pdf_metadata_to_tuple(meta))
        })
    }

    /// レンダリング時の CJK 代替フォントを設定する。
    ///
    /// kind は "sans"（ゴシック系・既定）か "serif"（明朝系）。
    /// data はフォントファイルのバイト列（TTF/OTF/TTC）、index は TTC 内の face 番号。
    fn set_fallback_font(&mut self, kind: &str, data: Vec<u8>, index: u32) -> PyResult<()> {
        let slot = match kind {
            "sans" => &mut self.fallback_fonts.sans,
            "serif" => &mut self.fallback_fonts.serif,
            _ => {
                return Err(PdfError::new_err(format!(
                    "kind は 'sans' か 'serif' で指定してください: {kind:?}"
                )));
            }
        };
        *slot = Some((Arc::new(data), index));
        Ok(())
    }

    /// CJK 代替フォントの設定をすべて解除する。
    fn clear_fallback_fonts(&mut self) {
        self.fallback_fonts = FallbackFonts::default();
    }

    /// 現在も暗号化されたままか（復号済みなら false）。
    fn is_encrypted(&self) -> bool {
        self.doc.is_encrypted()
    }

    /// ロード時点で暗号化されていたか（復号後も true のまま）。
    fn was_encrypted(&self) -> bool {
        self.doc.was_encrypted()
    }

    /// user password として正しいか（復号はしない）。
    fn authenticate_user_password(&self, password: &str) -> bool {
        self.doc.authenticate_user_password(password).is_ok()
    }

    /// owner password として正しいか（復号はしない）。
    fn authenticate_owner_password(&self, password: &str) -> bool {
        self.doc.authenticate_owner_password(password).is_ok()
    }

    /// ファイルパスへ保存する。
    fn save(&mut self, py: Python<'_>, path: &str) -> PyResult<()> {
        py.detach(|| {
            self.doc
                .save(path)
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// バイト列へ書き出す。
    fn save_bytes(&mut self, py: Python<'_>) -> PyResult<Vec<u8>> {
        py.detach(|| {
            let mut buffer = Vec::new();
            self.doc
                .save_to(&mut buffer)
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// object stream + xref stream（PDF 1.5+ 形式）でファイルへ保存する。
    ///
    /// lopdf 側が PDF バージョンの 1.5 への引き上げと xref 種別の切り替えを行い
    /// ドキュメント状態が変わるため、レンダリングキャッシュも無効化する。
    fn save_with_object_streams(&mut self, py: Python<'_>, path: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let file = std::fs::File::create(path)
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
            let mut writer = std::io::BufWriter::new(file);
            self.doc
                .save_with_options(&mut writer, modern_save_options())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))?;
            writer
                .into_inner()
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// object stream + xref stream（PDF 1.5+ 形式）でバイト列へ書き出す。
    fn save_bytes_with_object_streams(&mut self, py: Python<'_>) -> PyResult<Vec<u8>> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let mut buffer = Vec::new();
            self.doc
                .save_with_options(&mut buffer, modern_save_options())
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// ページ数を返す。
    fn page_count(&self) -> usize {
        self.doc.get_pages().len()
    }

    /// PDF バージョン文字列（例: "1.7"）を返す。
    fn version(&self) -> String {
        self.doc.version.clone()
    }

    /// Info 辞書の文字列項目を {キー: 値} で返す。
    fn get_metadata(&self) -> BTreeMap<String, String> {
        let mut result = BTreeMap::new();
        let Some(info) = self.info_dict() else {
            return result;
        };
        for (key, value) in info.iter() {
            let resolved = match value {
                Object::Reference(id) => match self.doc.get_object(*id) {
                    Ok(object) => object,
                    Err(_) => continue,
                },
                other => other,
            };
            if let Ok(text) = decode_text_string(resolved) {
                result.insert(String::from_utf8_lossy(key).into_owned(), text);
            }
        }
        result
    }

    /// Info 辞書の項目を設定する。値が空文字列なら項目を削除する。
    fn set_metadata(&mut self, key: &str, value: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let info_id = if let Ok(Object::Reference(id)) = self.doc.trailer.get(b"Info") {
            *id
        } else {
            // 直置き辞書は間接オブジェクトへ移し、無ければ新規作成する
            let existing = match self.doc.trailer.get(b"Info") {
                Ok(Object::Dictionary(dict)) => dict.clone(),
                _ => Dictionary::new(),
            };
            let id = self.doc.add_object(existing);
            self.doc.trailer.set("Info", id);
            id
        };
        let info = self
            .doc
            .get_object_mut(info_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        if value.is_empty() {
            info.remove(key.as_bytes());
        } else {
            info.set(key, text_string(value));
        }
        Ok(())
    }

    /// 指定ページ（1 始まり）を削除する。
    fn delete_pages(&mut self, page_numbers: Vec<u32>) {
        self.invalidate_hayro_pdf();
        self.doc.delete_pages(&page_numbers);
    }

    /// 指定ページ（1 始まり）のテキストを抽出する。
    ///
    /// hayro のインタープリタ（rust/src/extract.rs）でグリフの Unicode と位置を収集し、
    /// 読み順のテキストへ組み立てる。CJK 代替フォントの設定は抽出にも反映される。
    fn extract_text(&mut self, py: Python<'_>, page_numbers: Vec<u32>) -> PyResult<String> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let mut out = String::new();
            for number in &page_numbers {
                let page = number
                    .checked_sub(1)
                    .and_then(|index| pages.get(index as usize))
                    .ok_or_else(|| PdfError::new_err(format!("ページ {number} は存在しません")))?;
                out.push_str(&crate::extract::extract_page_text(
                    pdf,
                    page,
                    settings.clone(),
                ));
            }
            Ok(out)
        })
    }

    /// 指定ページ（1 始まり）のレイアウトを返す。
    ///
    /// 戻り値は (幅, 高さ, ブロック列)。ブロック = (bbox, 行列)、
    /// 行 = (bbox, スパン列, 語列)、スパン = (bbox, text, size, origin)、語 = (bbox, text)。
    #[allow(clippy::type_complexity)]
    fn extract_layout(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<(f64, f64, Vec<crate::extract::BlockTuple>)> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("ページ {page_number} は存在しません")))?;
            Ok(crate::extract::extract_page_layout(pdf, page, settings))
        })
    }

    /// 指定ページ（1 始まり）上に描画される画像を抽出する。
    ///
    /// 戻り値は (幅, 高さ, bbox, 形式 "jpeg"/"png", バイト列) のリスト。
    fn extract_images(
        &mut self,
        py: Python<'_>,
        page_number: u32,
    ) -> PyResult<Vec<crate::extract::ImageTuple>> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("ページ {page_number} は存在しません")))?;
            Ok(crate::extract::extract_page_images(pdf, page, settings))
        })
    }

    /// 指定ページ（1 始まり）をテキスト検索する（大文字小文字を区別しない）。
    fn search_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        needle: &str,
    ) -> PyResult<Vec<(f64, f64, f64, f64)>> {
        let settings = self.interpreter_settings();
        let pdf = self.hayro_view()?;
        py.detach(|| {
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("ページ {page_number} は存在しません")))?;
            Ok(crate::extract::search_page(pdf, page, settings, needle))
        })
    }

    /// 別ドキュメントの全ページを末尾に取り込む。
    fn merge(&mut self, py: Python<'_>, other: &Self) -> PyResult<()> {
        let count = u32::try_from(other.doc.get_pages().len())
            .map_err(|e| PdfError::new_err(e.to_string()))?;
        let all: Vec<u32> = (1..=count).collect();
        self.merge_pages(py, other, all, None)
    }

    /// 別ドキュメントの指定ページ（1 始まり・指定順）を取り込む。
    ///
    /// position は既存ページ列への挿入位置（0 始まり）。None なら末尾に追加する。
    /// 挿入時はページツリーを root 直下へ平坦化する。
    fn merge_pages(
        &mut self,
        py: Python<'_>,
        other: &Self,
        page_numbers: Vec<u32>,
        position: Option<usize>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            // 空ドキュメントでは先に Pages / Catalog の ID を確保し、取り込み元との衝突を防ぐ
            let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
            let subset = page_numbers.len() < other.doc.get_pages().len();
            let new_ids = self.transplant_pages(other, &page_numbers, pages_id)?;
            match position {
                None => self.append_pages(pages_id, new_ids)?,
                Some(_) => {
                    let order = self.spliced_page_order(new_ids, position);
                    self.rebuild_page_tree(pages_id, order)?;
                }
            }
            if subset || position.is_some() {
                // 対象外ページの資産や旧中間ノードを掃除する
                self.doc.prune_objects();
            }
            Ok(())
        })
    }

    /// 空ページを position（0 始まり、None で末尾）に挿入する。
    fn new_page(&mut self, position: Option<usize>, width: f32, height: f32) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let page_id = self.doc.add_object(dictionary! {
            "Type" => "Page",
            "Parent" => pages_id,
            "MediaBox" => Object::Array(vec![
                Object::Real(0.0),
                Object::Real(0.0),
                Object::Real(width),
                Object::Real(height),
            ]),
        });
        match position {
            None => self.append_pages(pages_id, vec![page_id]),
            Some(_) => {
                let order = self.spliced_page_order(vec![page_id], position);
                self.rebuild_page_tree(pages_id, order)?;
                self.doc.prune_objects();
                Ok(())
            }
        }
    }

    /// 指定ページ（1 始まり）の複製を position（0 始まり、None で末尾）に挿入する。
    ///
    /// ページ辞書は継承属性を焼き込んだ独立コピーになり、Contents / Resources は
    /// 元ページとオブジェクトを共有する。
    fn copy_page(&mut self, page_number: u32, position: Option<usize>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;
        let source_id = self.page_id(page_number)?;
        let mut dict = resolve_inherited_page_dict(&self.doc, source_id).map_err(to_py_err)?;
        dict.set("Parent", pages_id);
        let new_id = self.doc.add_object(Object::Dictionary(dict));
        match position {
            None => self.append_pages(pages_id, vec![new_id]),
            Some(_) => {
                let order = self.spliced_page_order(vec![new_id], position);
                self.rebuild_page_tree(pages_id, order)?;
                self.doc.prune_objects();
                Ok(())
            }
        }
    }

    /// 指定ページ（1 始まり）だけを指定順で残す。並べ替えにも使える。
    ///
    /// PDF のページツリーでは Parent が一意である必要があるため、
    /// 同一ページの重複指定（複製）は未対応。
    fn select(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let pages = self.doc.get_pages();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;

        // 同一ページの 2 回目以降は、継承属性焼き込み済みの複製ページを作る
        // （PDF のページツリーでは Parent が一意である必要があるため）
        let mut seen = HashSet::new();
        let mut ordered = Vec::with_capacity(page_numbers.len());
        for number in &page_numbers {
            let page_id = *pages
                .get(number)
                .ok_or_else(|| PdfError::new_err(format!("ページ {number} は存在しません")))?;
            let use_id = if seen.insert(page_id) {
                page_id
            } else {
                let dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
                self.doc.add_object(Object::Dictionary(dict))
            };
            ordered.push(use_id);
        }
        self.rebuild_page_tree(pages_id, ordered)?;

        // 参照されなくなったページ・中間ノードを掃除する
        self.doc.prune_objects();
        Ok(())
    }

    /// 指定ページ（1 始まり）を PNG 画像にレンダリングする。
    ///
    /// background は塗りつぶす背景色 RGBA（各 0-255）。None なら透明のまま。
    fn render_page_png(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<Vec<u8>> {
        if !scale.is_finite() || scale <= 0.0 {
            return Err(PdfError::new_err("scale は有限の正の値で指定してください"));
        }
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| {
                    PdfError::new_err(format!("ページ {page_number} は存在しません"))
                })?;
            let (page_width, page_height) = page.render_dimensions();
            let pixel_width = (f64::from(page_width) * f64::from(scale)).floor();
            let pixel_height = (f64::from(page_height) * f64::from(scale)).floor();
            if !pixel_width.is_finite()
                || !pixel_height.is_finite()
                || pixel_width < 1.0
                || pixel_height < 1.0
            {
                return Err(PdfError::new_err(
                    "scale が小さすぎるか、PDF のページサイズが不正です",
                ));
            }
            if pixel_width > f64::from(u16::MAX) || pixel_height > f64::from(u16::MAX) {
                return Err(PdfError::new_err(format!(
                    "描画サイズ {pixel_width:.0}x{pixel_height:.0} は1辺65535ピクセルの上限を超えています"
                )));
            }
            let total_pixels = (pixel_width as u64) * (pixel_height as u64);
            if total_pixels > MAX_RENDER_PIXELS {
                return Err(PdfError::new_err(format!(
                    "描画サイズ {pixel_width:.0}x{pixel_height:.0}（{total_pixels}画素）は{MAX_RENDER_PIXELS}画素の上限を超えています"
                )));
            }
            let mut render_settings = RenderSettings {
                x_scale: scale,
                y_scale: scale,
                ..Default::default()
            };
            if let Some((r, g, b, a)) = background {
                render_settings.bg_color = AlphaColor::from_rgba8(r, g, b, a);
            }
            let cache = RenderCache::new();
            let pixmap = render(page, &cache, &interpreter_settings, &render_settings);
            pixmap
                .into_png()
                .map_err(|e| PdfError::new_err(format!("failed to encode PNG: {e:?}")))
        })
    }

    /// 指定ページ（1 始まり）を SVG 文字列にレンダリングする。
    fn render_page_svg(&mut self, py: Python<'_>, page_number: u32) -> PyResult<String> {
        let interpreter_settings = self.interpreter_settings();
        py.detach(|| {
            let pdf = self.hayro_view()?;
            let pages = pdf.pages();
            let page = page_number
                .checked_sub(1)
                .and_then(|index| pages.get(index as usize))
                .ok_or_else(|| PdfError::new_err(format!("ページ {page_number} は存在しません")))?;
            let cache = hayro_svg::RenderCache::new();
            let settings = hayro_svg::SvgRenderSettings::default();
            Ok(hayro_svg::convert(
                page,
                &cache,
                &interpreter_settings,
                &settings,
            ))
        })
    }

    /// 目次（アウトライン）をフラットな (レベル, タイトル, 1 始まりページ番号) の列で返す。
    ///
    /// アウトラインが無い場合は空。ページに解決できない項目はスキップされる。
    fn get_toc(&self) -> PyResult<Vec<(u32, String, u32)>> {
        match self.doc.get_toc() {
            Ok(toc) => Ok(toc
                .toc
                .into_iter()
                .map(|t| (t.level as u32, t.title, t.page as u32))
                .collect()),
            // アウトライン自体が無い場合は空の目次として扱う
            // （catalog に Outlines キーが無いと DictKey が返る）
            Err(lopdf::Error::NoOutline) => Ok(Vec::new()),
            Err(lopdf::Error::DictKey(ref key)) if key == "Outlines" => Ok(Vec::new()),
            Err(e) => Err(to_py_err(e)),
        }
    }

    /// 目次を (レベル, タイトル, 1 始まりページ番号) の列で置き換える。空列で削除。
    ///
    /// レベルの検証（1 始まり・直前 +1 まで）は Python 側で行う。
    /// 非 ASCII タイトルは lopdf が UTF-16BE（BOM 付き）で書き込む。
    fn set_toc(&mut self, entries: Vec<(u32, String, u32)>) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        // 既存のアウトラインと構築用状態を捨てる
        self.doc.bookmarks.clear();
        self.doc.bookmark_table.clear();
        self.doc.max_bookmark_id = 0;
        if let Ok(catalog) = self.doc.catalog_mut() {
            catalog.remove(b"Outlines");
        }
        if entries.is_empty() {
            self.doc.prune_objects();
            return Ok(());
        }
        let pages = self.doc.get_pages();
        // parents[level - 1] = その階層の直近ブックマーク id
        let mut parents: Vec<u32> = Vec::new();
        for (level, title, page) in entries {
            let page_id = *pages
                .get(&page)
                .ok_or_else(|| PdfError::new_err(format!("ページ {page} は存在しません")))?;
            let level = level as usize;
            let parent = if level >= 2 {
                parents.get(level - 2).copied()
            } else {
                None
            };
            let id = self
                .doc
                .add_bookmark(Bookmark::new(title, [0.0, 0.0, 0.0], 0, page_id), parent);
            parents.truncate(level - 1);
            parents.push(id);
        }
        if let Some(outline_id) = self.doc.build_outline() {
            self.doc
                .catalog_mut()
                .map_err(to_py_err)?
                .set("Outlines", Object::Reference(outline_id));
        }
        // 旧アウトラインのオブジェクトを掃除する
        self.doc.prune_objects();
        Ok(())
    }

    /// AES-256 で暗号化してファイルへ保存する。このドキュメント自体は平文のまま。
    fn save_encrypted(
        &self,
        py: Python<'_>,
        path: &str,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<()> {
        py.detach(|| {
            let mut cloned = self.encrypted_clone(
                user_password,
                owner_password,
                permissions,
                file_encryption_key,
            )?;
            cloned
                .save(path)
                .map(|_| ())
                .map_err(|e| PdfError::new_err(format!("failed to save {path}: {e}")))
        })
    }

    /// AES-256 で暗号化してバイト列へ書き出す。このドキュメント自体は平文のまま。
    fn save_bytes_encrypted(
        &self,
        py: Python<'_>,
        user_password: &str,
        owner_password: &str,
        permissions: u64,
        file_encryption_key: &[u8],
    ) -> PyResult<Vec<u8>> {
        py.detach(|| {
            let mut cloned = self.encrypted_clone(
                user_password,
                owner_password,
                permissions,
                file_encryption_key,
            )?;
            let mut buffer = Vec::new();
            cloned
                .save_to(&mut buffer)
                .map_err(|e| PdfError::new_err(e.to_string()))?;
            Ok(buffer)
        })
    }

    /// 指定ページ（1 始まり）の回転角（継承解決済み、0..360 に正規化）を返す。
    fn get_page_rotation(&self, page_number: u32) -> PyResult<i64> {
        let page_id = self.page_id(page_number)?;
        match self.resolve_page_attr(page_id, b"Rotate")? {
            Some(obj) => Ok(obj.as_i64().map_err(to_py_err)?.rem_euclid(360)),
            None => Ok(0),
        }
    }

    /// 指定ページ（1 始まり）の回転角を設定する（値の検証は Python 側）。
    fn set_page_rotation(&mut self, page_number: u32, rotation: i64) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let page_id = self.page_id(page_number)?;
        let dict = self
            .doc
            .get_object_mut(page_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        dict.set("Rotate", rotation);
        Ok(())
    }

    /// 指定ページ（1 始まり）のボックス（MediaBox / CropBox 等、継承解決済み）を返す。
    /// 設定されていなければ None。
    fn get_page_box(&self, page_number: u32, key: &str) -> PyResult<Option<(f64, f64, f64, f64)>> {
        let page_id = self.page_id(page_number)?;
        let Some(obj) = self.resolve_page_attr(page_id, key.as_bytes())? else {
            return Ok(None);
        };
        let arr = obj.as_array().map_err(to_py_err)?;
        if arr.len() != 4 {
            return Err(PdfError::new_err(format!(
                "{key} は 4 要素の配列である必要があります（{} 要素）",
                arr.len()
            )));
        }
        let mut values = [0f64; 4];
        for (slot, item) in values.iter_mut().zip(arr) {
            let resolved = match item {
                Object::Reference(id) => self.doc.get_object(*id).map_err(to_py_err)?,
                other => other,
            };
            *slot = f64::from(resolved.as_float().map_err(to_py_err)?);
        }
        Ok(Some((values[0], values[1], values[2], values[3])))
    }

    /// 指定ページ（1 始まり）のボックスを設定する（矩形の検証は Python 側）。
    fn set_page_box(
        &mut self,
        page_number: u32,
        key: &str,
        x0: f64,
        y0: f64,
        x1: f64,
        y1: f64,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let page_id = self.page_id(page_number)?;
        let dict = self
            .doc
            .get_object_mut(page_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        dict.set(
            key,
            Object::Array(vec![
                Object::Real(x0 as f32),
                Object::Real(y0 as f32),
                Object::Real(x1 as f32),
                Object::Real(y1 as f32),
            ]),
        );
        Ok(())
    }

    /// ストリームを圧縮する。
    fn compress(&mut self, py: Python<'_>) {
        self.invalidate_hayro_pdf();
        py.detach(|| self.doc.compress());
    }

    /// ストリームを展開する。
    fn decompress(&mut self, py: Python<'_>) {
        self.invalidate_hayro_pdf();
        py.detach(|| self.doc.decompress());
    }

    /// 参照されていないオブジェクトを削除する。
    fn prune_objects(&mut self) {
        self.invalidate_hayro_pdf();
        self.doc.prune_objects();
    }
}
