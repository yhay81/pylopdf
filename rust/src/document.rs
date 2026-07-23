//! lopdf::Document の Python バインディング。
//!
//! 型変換とエラー変換のみを担う薄い層。使いやすい API は Python 側の
//! `pylopdf.Document` が提供する。

use std::collections::{BTreeMap, HashSet};
use std::sync::{Arc, Mutex};

use hayro::hayro_interpret::font::{FallbackFontQuery, FontData, FontQuery};
use hayro::hayro_interpret::hayro_cmap::CidFamily;
use hayro::hayro_interpret::{InterpreterSettings, InterpreterWarning};
use hayro::hayro_syntax::Pdf;
use hayro::vello_cpu::color::AlphaColor;
use hayro::{RenderCache, RenderSettings, render};
use lopdf::encryption::crypt_filters::{Aes256CryptFilter, CryptFilter};
use lopdf::encryption::{EncryptionState, EncryptionVersion, Permissions};
use lopdf::{
    Bookmark, Dictionary, Document, LoadOptions, Object, ObjectId, PdfMetadata, SaveOptions,
    Stream, decode_text_string, dictionary, text_string,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::draw;
use crate::ocr;

/// read_annotations が返す 1 注釈分のタプル（Subtype, 表示座標 Rect, Contents, URI）。
type AnnotationTuple = (String, (f64, f64, f64, f64), Option<String>, Option<String>);

/// フィールド木の走査 1 ノード分（ObjectId, 接頭名, 継承 FT, 継承 Ff, 継承 V）。
type FieldNode = (ObjectId, String, Option<String>, i64, Option<Object>);

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

/// 辞書のボックス配列（間接参照許容）を正規化済みの [x0, y0, x1, y1] で読む。
fn resolve_box(doc: &Document, dict: &Dictionary, key: &[u8]) -> Option<[f64; 4]> {
    let obj = dict.get(key).ok()?;
    let obj = match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?,
        other => other,
    };
    let arr = obj.as_array().ok()?;
    if arr.len() != 4 {
        return None;
    }
    let mut v = [0f64; 4];
    for (slot, item) in v.iter_mut().zip(arr) {
        let resolved = match item {
            Object::Reference(id) => doc.get_object(*id).ok()?,
            other => other,
        };
        *slot = f64::from(resolved.as_float().ok()?);
    }
    Some([
        v[0].min(v[2]),
        v[1].min(v[3]),
        v[0].max(v[2]),
        v[1].max(v[3]),
    ])
}

/// XMP テキストから属性形式（key="v"）か要素形式（<key>v</key>）の値を取り出す。
fn xmp_value(xmp: &str, key: &str) -> Option<String> {
    let idx = xmp.find(key)?;
    let rest = xmp[idx + key.len()..].trim_start();
    if let Some(r) = rest.strip_prefix('=') {
        let r = r.trim_start();
        let r = r.strip_prefix('"').or_else(|| r.strip_prefix('\''))?;
        let end = r.find(['"', '\''])?;
        return Some(r[..end].to_owned());
    }
    if let Some(r) = rest.strip_prefix('>') {
        let end = r.find('<')?;
        return Some(r[..end].trim().to_owned());
    }
    None
}

/// hayro の Pixmap をストレートアルファの RGBA8 バイト列へ変換する。
fn rgba_bytes(pixmap: hayro::vello_cpu::Pixmap) -> Vec<u8> {
    let pixels = pixmap.take_unpremultiplied();
    let mut out = Vec::with_capacity(pixels.len() * 4);
    for px in pixels {
        out.extend_from_slice(&[px.r, px.g, px.b, px.a]);
    }
    out
}

/// 間接参照を許容して辞書を取り出す（クローン）。
fn deref_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
        Object::Dictionary(d) => Some(d.clone()),
        _ => None,
    }
}

/// 間接参照を許容して整数値を読む。
fn resolve_i64(doc: &Document, obj: &Object) -> Option<i64> {
    match obj {
        Object::Reference(id) => doc.get_object(*id).ok()?.as_i64().ok(),
        other => other.as_i64().ok(),
    }
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
    /// 直近のレンダリング・抽出で hayro が出した警告（interpreter_settings の sink が
    /// 書き込み、take_warnings で取り出す）。
    pending_warnings: Arc<Mutex<Vec<String>>>,
}

impl _Document {
    /// lopdf::Document から（fallback フォント未設定の状態で）構築する。
    fn from_doc(doc: Document) -> Self {
        Self {
            doc,
            fallback_fonts: FallbackFonts::default(),
            hayro_pdf: None,
            pending_warnings: Arc::new(Mutex::new(Vec::new())),
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

    /// ページの表示ジオメトリ（CropBox → MediaBox → A4 の順で決まる矩形と、正規化済み回転）。
    fn page_display_geometry(&self, page_number: u32) -> PyResult<([f64; 4], i64)> {
        let rotation = self.get_page_rotation(page_number)?;
        let boxed = self
            .get_page_box(page_number, "CropBox")?
            .or(self.get_page_box(page_number, "MediaBox")?)
            .unwrap_or((0.0, 0.0, 595.0, 842.0));
        let (x0, y0, x1, y1) = boxed;
        Ok(([x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)], rotation))
    }

    /// ページ辞書へ継承属性を焼き込む。
    ///
    /// lopdf の add_xobject はページ自身に /Resources が無いと空辞書を新設して
    /// 親ツリーの継承 Resources を影で潰すため、描き込み系の前処理として必須。
    fn bake_page_attrs(&mut self, page_id: ObjectId) -> PyResult<()> {
        let dict = resolve_inherited_page_dict(&self.doc, page_id).map_err(to_py_err)?;
        self.doc.objects.insert(page_id, Object::Dictionary(dict));
        Ok(())
    }

    /// AcroForm フィールド木を平坦化して (完全名, フィールド ObjectId, FT, Ff, V) を集める。
    ///
    /// FT / Ff / V は親から継承される。完全名は /T をドットで連結したもの。
    /// 終端 = /T 付きの子を持たず FT が解決できたノード。
    fn collect_form_fields(&self) -> Vec<(String, ObjectId, String, i64, Option<Object>)> {
        let Some(acroform) = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"AcroForm").ok().cloned())
            .and_then(|o| deref_dict(&self.doc, &o))
        else {
            return Vec::new();
        };
        let Ok(fields) = acroform.get(b"Fields").and_then(Object::as_array) else {
            return Vec::new();
        };
        let mut out = Vec::new();
        // (ObjectId, 接頭名, 継承 FT, 継承 Ff, 継承 V)
        let mut stack: Vec<FieldNode> = fields
            .iter()
            .filter_map(|f| f.as_reference().ok())
            .map(|id| (id, String::new(), None, 0, None))
            .collect();
        let mut visited = HashSet::new();
        while let Some((id, prefix, inh_ft, inh_ff, inh_v)) = stack.pop() {
            if !visited.insert(id) || visited.len() > 4096 {
                continue;
            }
            let Ok(dict) = self.doc.get_object(id).and_then(Object::as_dict) else {
                continue;
            };
            let name = match dict.get(b"T").ok().and_then(|t| decode_text_string(t).ok()) {
                Some(t) if prefix.is_empty() => t,
                Some(t) => format!("{prefix}.{t}"),
                None => prefix.clone(),
            };
            let ft = dict
                .get(b"FT")
                .and_then(Object::as_name)
                .ok()
                .map(|n| String::from_utf8_lossy(n).into_owned())
                .or(inh_ft);
            let ff = dict
                .get(b"Ff")
                .ok()
                .and_then(|o| resolve_i64(&self.doc, o))
                .unwrap_or(inh_ff);
            let v = dict.get(b"V").ok().cloned().or(inh_v);
            // /T を持つ子 = 下位フィールド、持たない子 = ウィジェット
            let child_fields: Vec<ObjectId> = dict
                .get(b"Kids")
                .and_then(Object::as_array)
                .map(|kids| {
                    kids.iter()
                        .filter_map(|k| k.as_reference().ok())
                        .filter(|kid_id| {
                            self.doc
                                .get_object(*kid_id)
                                .and_then(Object::as_dict)
                                .is_ok_and(|d| d.has(b"T"))
                        })
                        .collect()
                })
                .unwrap_or_default();
            if child_fields.is_empty() {
                if let Some(ft) = ft {
                    out.push((name, id, ft, ff, v));
                }
            } else {
                for kid in child_fields {
                    stack.push((kid, name.clone(), ft.clone(), ff, v.clone()));
                }
            }
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    }

    /// フィールドのウィジェット注釈 ObjectId 列を返す（Kids が無ければフィールド自身）。
    fn field_widgets(&self, field_id: ObjectId) -> Vec<ObjectId> {
        let Ok(dict) = self.doc.get_object(field_id).and_then(Object::as_dict) else {
            return vec![field_id];
        };
        let widgets: Vec<ObjectId> = dict
            .get(b"Kids")
            .and_then(Object::as_array)
            .map(|kids| {
                kids.iter()
                    .filter_map(|k| k.as_reference().ok())
                    .filter(|kid_id| {
                        self.doc
                            .get_object(*kid_id)
                            .and_then(Object::as_dict)
                            .is_ok_and(|d| !d.has(b"T"))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if widgets.is_empty() {
            vec![field_id]
        } else {
            widgets
        }
    }

    /// AcroForm 辞書に NeedAppearances = true を立てる（間接参照でも対応）。
    fn set_need_appearances(&mut self) -> PyResult<()> {
        let acroform_ref = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"AcroForm").ok())
            .and_then(|a| a.as_reference().ok());
        match acroform_ref {
            Some(id) => {
                let acroform = self
                    .doc
                    .get_object_mut(id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", true);
            }
            None => {
                let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
                let acroform = catalog
                    .get_mut(b"AcroForm")
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                acroform.set("NeedAppearances", true);
            }
        }
        Ok(())
    }

    /// ページの /Annots 配列へ注釈オブジェクトの参照を追加する（配列が間接参照でも対応）。
    fn push_page_annotation(&mut self, page_id: ObjectId, annot_id: ObjectId) -> PyResult<()> {
        let array_ref = {
            let page = self
                .doc
                .get_object(page_id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?;
            page.get(b"Annots").ok().and_then(|a| a.as_reference().ok())
        };
        match array_ref {
            Some(arr_id) => {
                let arr = self
                    .doc
                    .get_object_mut(arr_id)
                    .and_then(Object::as_array_mut)
                    .map_err(to_py_err)?;
                arr.push(Object::Reference(annot_id));
            }
            None => {
                let page = self
                    .doc
                    .get_object_mut(page_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                let mut arr = match page.get(b"Annots").and_then(Object::as_array) {
                    Ok(existing) => existing.clone(),
                    Err(_) => Vec::new(),
                };
                arr.push(Object::Reference(annot_id));
                page.set("Annots", arr);
            }
        }
        Ok(())
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

    /// fallback フォント設定と警告 sink を反映した InterpreterSettings を組み立てる。
    fn interpreter_settings(&self) -> InterpreterSettings {
        let mut settings = InterpreterSettings::default();
        if self.fallback_fonts.sans.is_some() || self.fallback_fonts.serif.is_some() {
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
        }
        // hayro の警告を pending_warnings へ集める（同一メッセージは 1 回だけ）
        let sink = Arc::clone(&self.pending_warnings);
        settings.warning_sink = Arc::new(move |warning| {
            let message = match warning {
                InterpreterWarning::UnsupportedFont => {
                    "未対応のフォント形式があり、一部のグリフを処理できませんでした"
                }
                InterpreterWarning::ImageDecodeFailure => "画像のデコードに失敗しました",
            };
            if let Ok(mut pending) = sink.lock()
                && !pending.iter().any(|m| m == message)
            {
                pending.push(message.to_owned());
            }
        });
        settings
    }

    /// ページを検証付きでレンダリングして hayro の Pixmap を返す
    /// （render_page_png / render_page_pixmap の共通実装）。
    fn render_pixmap_impl(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<hayro::vello_cpu::Pixmap> {
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
            Ok(render(page, &cache, &interpreter_settings, &render_settings))
        })
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
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        // PNG エンコードはページによってはラスタライズより高コストなので GIL を解放し、
        // 圧縮は Fast（fdeflate）を使う。既定の Balanced はサイズ約 1 割減のために
        // 数十倍遅く、render_page の主コストが PNG になってしまう（bench で実測）
        py.detach(|| {
            let width = u32::from(pixmap.width());
            let height = u32::from(pixmap.height());
            let data = rgba_bytes(pixmap);
            crate::extract::encode_png(
                width,
                height,
                png::ColorType::Rgba,
                &data,
                png::Compression::Fast,
            )
            .ok_or_else(|| PdfError::new_err("failed to encode PNG"))
        })
    }

    /// 指定ページ（1 始まり）をレンダリングして Pixmap（ストレート RGBA8）を返す。
    fn render_page_pixmap(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        scale: f32,
        background: Option<(u8, u8, u8, u8)>,
    ) -> PyResult<crate::pixmap::Pixmap> {
        let pixmap = self.render_pixmap_impl(py, page_number, scale, background)?;
        let width = u32::from(pixmap.width());
        let height = u32::from(pixmap.height());
        // アルファ非乗算化 + バイト列化も大画像では重いので GIL を解放する
        let data = py.detach(|| rgba_bytes(pixmap));
        Ok(crate::pixmap::Pixmap {
            width,
            height,
            data,
        })
    }

    /// 直近の操作で溜まった hayro の警告メッセージを取り出す（取り出すと空になる）。
    fn take_warnings(&mut self) -> Vec<String> {
        self.pending_warnings
            .lock()
            .map(|mut pending| std::mem::take(&mut *pending))
            .unwrap_or_default()
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

    /// 指定ページ（1 始まり）の表示座標 rect へ画像（JPEG / PNG のバイト列）を描き込む。
    ///
    /// rect は左上原点の表示空間（page.rect と同じ系。回転ページも表示上の位置で指定する）。
    /// 既存コンテンツには触れず、新しいコンテンツストリームの追加だけで描く。
    fn insert_image(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        data: Vec<u8>,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let parts = draw::parse_image(&data).map_err(PdfError::new_err)?.ok_or_else(|| {
                PdfError::new_err(
                    "対応していない画像形式です（JPEG か PNG を渡してください。他形式は Pillow 等で変換できます）",
                )
            })?;
            let content = draw::PlacedContent::Image {
                width: parts.width,
                height: parts.height,
            };
            let matrix = draw::placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                &content,
                keep_proportion,
            );
            let xobj_id = draw::add_image_xobject(&mut self.doc, parts).map_err(PdfError::new_err)?;
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloIm{}", xobj_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), xobj_id)
                .map_err(to_py_err)?;
            draw::push_content(&mut self.doc, page_id, draw::draw_ops(matrix, &name), overlay).map_err(to_py_err)
        })
    }

    /// other の指定ページ（1 始まり）を Form XObject として取り込み、表示座標 rect へ重ねる。
    ///
    /// merge と同じ流儀で取り込み元のオブジェクトを番号替えして持ち込み、
    /// ページコンテンツは Form XObject に包んで「ベクタのまま」配置する。
    // Python 側シグネチャをそのまま写す境界メソッドのため引数数は許容する
    #[allow(clippy::too_many_arguments)]
    fn show_pdf_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        other: &Self,
        src_page_number: u32,
        keep_proportion: bool,
        overlay: bool,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let starting_id = self
                .doc
                .max_id
                .checked_add(1)
                .ok_or_else(|| PdfError::new_err("PDF オブジェクト ID が上限に達しています"))?;
            let mut other_doc = other.doc.clone();
            other_doc.renumber_objects_with(starting_id);
            let src_id = *other_doc.get_pages().get(&src_page_number).ok_or_else(|| {
                PdfError::new_err(format!(
                    "取り込み元のページ {src_page_number} は存在しません"
                ))
            })?;
            let src_dict = resolve_inherited_page_dict(&other_doc, src_id).map_err(to_py_err)?;

            // 取り込み元ページの表示ジオメトリ（CropBox → MediaBox → A4、回転は 0..360）
            let src_crop = resolve_box(&other_doc, &src_dict, b"CropBox")
                .or_else(|| resolve_box(&other_doc, &src_dict, b"MediaBox"))
                .unwrap_or([0.0, 0.0, 595.0, 842.0]);
            let src_rotation = src_dict
                .get(b"Rotate")
                .ok()
                .and_then(|o| resolve_i64(&other_doc, o))
                .unwrap_or(0)
                .rem_euclid(360);

            // 元コンテンツの q/Q 不均衡から自衛しつつ Form にまとめる（中身は再エンコードしない）
            let mut form_content = b"q\n".to_vec();
            form_content.extend_from_slice(&other_doc.get_page_content(src_id));
            form_content.extend_from_slice(b"\nQ\n");

            let mut form_dict = dictionary! {
                "Type" => "XObject",
                "Subtype" => "Form",
                "FormType" => 1,
                "BBox" => Object::Array(src_crop.iter().map(|&v| Object::Real(v as f32)).collect()),
            };
            if let Ok(res) = src_dict.get(b"Resources") {
                form_dict.set("Resources", res.clone());
            }
            if let Ok(group) = src_dict.get(b"Group") {
                form_dict.set("Group", group.clone());
            }

            // ページツリー以外のオブジェクト（Resources が参照する資産）を取り込む
            let new_max_id = other_doc.max_id;
            for (id, object) in other_doc.objects {
                match object.type_name().unwrap_or(b"") {
                    b"Catalog" | b"Pages" | b"Page" => {}
                    _ => {
                        self.doc.objects.insert(id, object);
                    }
                }
            }
            self.doc.max_id = new_max_id;

            let form_id = self
                .doc
                .add_object(Stream::new(form_dict, form_content).with_compression(false));
            let content = draw::PlacedContent::Form {
                crop: src_crop,
                rotation: src_rotation,
            };
            let matrix = draw::placement_matrix(
                crop,
                rotation,
                [rect.0, rect.1, rect.2, rect.3],
                &content,
                keep_proportion,
            );
            self.bake_page_attrs(page_id)?;
            let name = format!("PyloFm{}", form_id.0);
            self.doc
                .add_xobject(page_id, name.as_bytes(), form_id)
                .map_err(to_py_err)?;
            draw::push_content(
                &mut self.doc,
                page_id,
                draw::draw_ops(matrix, &name),
                overlay,
            )
            .map_err(to_py_err)
        })
    }

    /// 指定ページ（1 始まり）の注釈を読み取る。
    ///
    /// 各要素は (Subtype, 表示座標の Rect, Contents, URI)。Rect はページの回転を
    /// 反映した表示空間（左上原点）。Contents / URI は無ければ None。
    fn read_annotations(&self, page_number: u32) -> PyResult<Vec<AnnotationTuple>> {
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        let page = self
            .doc
            .get_object(page_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let annots = match page.get(b"Annots") {
            Ok(Object::Reference(id)) => {
                match self.doc.get_object(*id).and_then(Object::as_array) {
                    Ok(arr) => arr.clone(),
                    Err(_) => return Ok(Vec::new()),
                }
            }
            Ok(Object::Array(arr)) => arr.clone(),
            _ => return Ok(Vec::new()),
        };
        let mut out = Vec::new();
        for item in annots {
            let dict = match &item {
                Object::Reference(id) => match self.doc.get_object(*id).and_then(Object::as_dict) {
                    Ok(d) => d,
                    Err(_) => continue,
                },
                Object::Dictionary(d) => d,
                _ => continue,
            };
            let subtype = match dict.get(b"Subtype").and_then(Object::as_name) {
                Ok(name) => String::from_utf8_lossy(name).into_owned(),
                Err(_) => continue,
            };
            let Some(rect) = resolve_box(&self.doc, dict, b"Rect") else {
                continue;
            };
            let display = draw::pdf_rect_to_display(crop, rotation, rect);
            let contents = dict
                .get(b"Contents")
                .ok()
                .map(|o| match o {
                    Object::Reference(id) => self.doc.get_object(*id).unwrap_or(o),
                    other => other,
                })
                .and_then(|o| decode_text_string(o).ok());
            let uri = dict
                .get(b"A")
                .ok()
                .and_then(|a| match a {
                    Object::Reference(id) => {
                        self.doc.get_object(*id).and_then(Object::as_dict).ok()
                    }
                    Object::Dictionary(d) => Some(d),
                    _ => None,
                })
                .filter(|action| matches!(action.get(b"S").and_then(Object::as_name), Ok(b"URI")))
                .and_then(|action| action.get(b"URI").ok())
                .and_then(|o| o.as_str().ok())
                .map(|s| String::from_utf8_lossy(s).into_owned());
            out.push((
                subtype,
                (display[0], display[1], display[2], display[3]),
                contents,
                uri,
            ));
        }
        Ok(out)
    }

    /// 指定ページ（1 始まり）へハイライト注釈を追加する。
    ///
    /// rects は表示座標。QuadPoints（Acrobat 互換のジグザグ順）と、hayro を含む
    /// ビューアで見た目を保証する外観ストリーム（AP /N、Multiply ブレンド）を生成する。
    fn add_highlight_annotation(
        &mut self,
        page_number: u32,
        rects: Vec<(f64, f64, f64, f64)>,
        color: (f64, f64, f64),
        opacity: f64,
        content: Option<String>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;

        let quads: Vec<[(f64, f64); 4]> = rects
            .iter()
            .map(|&(x0, y0, x1, y1)| draw::display_rect_quad_pdf(crop, rotation, [x0, y0, x1, y1]))
            .collect();
        let all_points: Vec<(f64, f64)> = quads.iter().flatten().copied().collect();
        let bbox = draw::bounding_rect(&all_points);

        // 外観ストリーム: BBox = 注釈 Rect（ページ空間座標のまま描く）
        let gs_id = self.doc.add_object(dictionary! {
            "Type" => "ExtGState",
            "BM" => Object::Name(b"Multiply".to_vec()),
            "CA" => Object::Real(opacity as f32),
            "ca" => Object::Real(opacity as f32),
        });
        let form_dict = dictionary! {
            "Type" => "XObject",
            "Subtype" => "Form",
            "FormType" => 1,
            "BBox" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "Group" => dictionary! { "Type" => "Group", "S" => "Transparency" },
            "Resources" => dictionary! {
                "ExtGState" => dictionary! { "PyloGS" => Object::Reference(gs_id) },
            },
        };
        let ap_id = self.doc.add_object(
            Stream::new(form_dict, draw::highlight_ap_ops(&quads, color)).with_compression(false),
        );

        let quad_points: Vec<Object> = quads
            .iter()
            .flatten()
            .flat_map(|&(x, y)| [Object::Real(x as f32), Object::Real(y as f32)])
            .collect();
        let mut annot = dictionary! {
            "Type" => "Annot",
            "Subtype" => "Highlight",
            "Rect" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "QuadPoints" => Object::Array(quad_points),
            "C" => Object::Array(vec![
                Object::Real(color.0 as f32),
                Object::Real(color.1 as f32),
                Object::Real(color.2 as f32),
            ]),
            "CA" => Object::Real(opacity as f32),
            // 印刷対象フラグ
            "F" => 4,
            "P" => page_id,
            "AP" => dictionary! { "N" => Object::Reference(ap_id) },
        };
        if let Some(text) = content {
            annot.set("Contents", text_string(&text));
        }
        let annot_id = self.doc.add_object(annot);
        self.push_page_annotation(page_id, annot_id)
    }

    /// 指定ページ（1 始まり）の表示座標 rect へ URI リンク注釈を追加する。
    fn add_link_annotation(
        &mut self,
        page_number: u32,
        rect: (f64, f64, f64, f64),
        uri: String,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        let quad = draw::display_rect_quad_pdf(crop, rotation, [rect.0, rect.1, rect.2, rect.3]);
        let bbox = draw::bounding_rect(&quad);
        let annot_id = self.doc.add_object(dictionary! {
            "Type" => "Annot",
            "Subtype" => "Link",
            "Rect" => Object::Array(bbox.iter().map(|&v| Object::Real(v as f32)).collect()),
            "Border" => Object::Array(vec![0.into(), 0.into(), 0.into()]),
            "F" => 4,
            "P" => page_id,
            "A" => dictionary! {
                "Type" => "Action",
                "S" => "URI",
                "URI" => Object::string_literal(uri),
            },
        });
        self.push_page_annotation(page_id, annot_id)
    }

    /// 添付ファイル（EmbeddedFiles 名前ツリー）の (名前, FileSpec の ObjectId) を集める。
    ///
    /// /Kids 分割された名前ツリーも再帰的に辿る（深さ・循環はガード）。
    /// FileSpec がインライン辞書の場合はオブジェクト化して id を返す。
    fn collect_embedded_files(&mut self) -> Vec<(String, ObjectId)> {
        fn node_dict(doc: &Document, obj: &Object) -> Option<Dictionary> {
            match obj {
                Object::Reference(id) => doc.get_object(*id).ok()?.as_dict().ok().cloned(),
                Object::Dictionary(d) => Some(d.clone()),
                _ => None,
            }
        }
        let Some(root) = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"Names").ok().cloned())
            .and_then(|names| node_dict(&self.doc, &names))
            .and_then(|names| names.get(b"EmbeddedFiles").ok().cloned())
            .and_then(|ef| node_dict(&self.doc, &ef))
        else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut stack = vec![(root, 0usize)];
        while let Some((node, depth)) = stack.pop() {
            if depth > 32 {
                continue;
            }
            if let Ok(pairs) = node.get(b"Names").and_then(Object::as_array) {
                for pair in pairs.chunks(2) {
                    let [key, value] = pair else { continue };
                    let Ok(name) = decode_text_string(key) else {
                        continue;
                    };
                    let id = match value {
                        Object::Reference(id) => *id,
                        Object::Dictionary(d) => self.doc.add_object(Object::Dictionary(d.clone())),
                        _ => continue,
                    };
                    out.push((name, id));
                }
            }
            if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
                for kid in kids.clone() {
                    if let Some(dict) = node_dict(&self.doc, &kid) {
                        stack.push((dict, depth + 1));
                    }
                }
            }
        }
        out
    }

    /// EmbeddedFiles 名前ツリーを平坦な 1 ノードで書き戻す（他の名前ツリーは保存）。
    fn write_embedded_files(&mut self, mut entries: Vec<(String, ObjectId)>) -> PyResult<()> {
        entries.sort_by(|a, b| a.0.cmp(&b.0));
        let mut flat = Vec::with_capacity(entries.len() * 2);
        for (name, id) in entries {
            flat.push(text_string(&name));
            flat.push(Object::Reference(id));
        }
        let tree = Object::Dictionary(dictionary! { "Names" => Object::Array(flat) });
        // /Names が間接参照ならその実体を、インラインなら Catalog 内をその場で書き換える
        let names_ref = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"Names").ok())
            .and_then(|n| n.as_reference().ok());
        match names_ref {
            Some(id) => {
                let names = self
                    .doc
                    .get_object_mut(id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                names.set("EmbeddedFiles", tree);
            }
            None => {
                let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
                if !catalog.has(b"Names") {
                    catalog.set("Names", Dictionary::new());
                }
                let names = catalog
                    .get_mut(b"Names")
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                names.set("EmbeddedFiles", tree);
            }
        }
        Ok(())
    }

    /// XMP メタデータの PDF/A 宣言（pdfaid:part / conformance）を読み取る。
    ///
    /// 自己申告の読み取りであり、準拠の検証ではない（検証は veraPDF の領分）。
    /// PDF/A-4 は conformance を持たないため空文字列になる。
    fn pdfa_claim(&self) -> Option<(i64, String)> {
        let catalog = self.doc.catalog().ok()?;
        let meta_ref = catalog.get(b"Metadata").ok()?.as_reference().ok()?;
        let stream = self.doc.get_object(meta_ref).ok()?.as_stream().ok()?;
        let data = stream
            .decompressed_content()
            .unwrap_or_else(|_| stream.content.clone());
        let xmp = String::from_utf8_lossy(&data);
        let part: i64 = xmp_value(&xmp, "pdfaid:part")?.parse().ok()?;
        let conformance = xmp_value(&xmp, "pdfaid:conformance").unwrap_or_default();
        Some((part, conformance))
    }

    /// ページラベル定義（PageLabels 番号ツリー）を読む。
    ///
    /// 各要素は (開始ページ index, style, prefix, 開始番号)。Kids 分割も再帰で辿り、
    /// 開始ページ順にソートして返す。
    fn get_page_labels(&self) -> Vec<(i64, Option<String>, Option<String>, i64)> {
        let Some(root) = self
            .doc
            .catalog()
            .ok()
            .and_then(|c| c.get(b"PageLabels").ok().cloned())
            .and_then(|o| deref_dict(&self.doc, &o))
        else {
            return Vec::new();
        };
        let mut out = Vec::new();
        let mut stack = vec![(root, 0usize)];
        while let Some((node, depth)) = stack.pop() {
            if depth > 32 {
                continue;
            }
            if let Ok(nums) = node.get(b"Nums").and_then(Object::as_array) {
                for pair in nums.chunks(2) {
                    let [key, value] = pair else { continue };
                    let Some(start) = resolve_i64(&self.doc, key) else {
                        continue;
                    };
                    let Some(label) = deref_dict(&self.doc, value) else {
                        continue;
                    };
                    let style = label
                        .get(b"S")
                        .and_then(Object::as_name)
                        .ok()
                        .map(|n| String::from_utf8_lossy(n).into_owned());
                    let prefix = label
                        .get(b"P")
                        .ok()
                        .and_then(|o| decode_text_string(o).ok());
                    let st = label
                        .get(b"St")
                        .ok()
                        .and_then(|o| resolve_i64(&self.doc, o))
                        .unwrap_or(1);
                    out.push((start, style, prefix, st));
                }
            }
            if let Ok(kids) = node.get(b"Kids").and_then(Object::as_array) {
                for kid in kids.clone() {
                    if let Some(dict) = deref_dict(&self.doc, &kid) {
                        stack.push((dict, depth + 1));
                    }
                }
            }
        }
        out.sort_by_key(|(start, _, _, _)| *start);
        out
    }

    /// ページラベル定義を平坦な番号ツリーで書き込む（空リストで削除。検証は Python 側）。
    fn set_page_labels(
        &mut self,
        labels: Vec<(i64, Option<String>, Option<String>, i64)>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let mut nums = Vec::with_capacity(labels.len() * 2);
        for (start, style, prefix, st) in labels {
            let mut label = Dictionary::new();
            if let Some(s) = style {
                label.set("S", Object::Name(s.into_bytes()));
            }
            if let Some(p) = prefix {
                label.set("P", text_string(&p));
            }
            if st != 1 {
                label.set("St", st);
            }
            nums.push(Object::Integer(start));
            nums.push(Object::Dictionary(label));
        }
        let catalog = self.doc.catalog_mut().map_err(to_py_err)?;
        if nums.is_empty() {
            catalog.remove(b"PageLabels");
        } else {
            catalog.set("PageLabels", dictionary! { "Nums" => Object::Array(nums) });
        }
        Ok(())
    }

    /// AcroForm フィールドの一覧（完全名, 種別, 値）を返す。
    ///
    /// 種別は text / checkbox / radio / button / combobox / listbox / signature。
    /// 値は文字列化した /V（チェックボックスは "Yes"/"Off" などの状態名。無ければ None）。
    fn get_form_fields(&self) -> Vec<(String, String, Option<String>)> {
        self.collect_form_fields()
            .into_iter()
            .map(|(name, _, ft, ff, v)| {
                let kind = match ft.as_str() {
                    "Tx" => "text",
                    "Sig" => "signature",
                    "Ch" => {
                        if ff & (1 << 17) != 0 {
                            "combobox"
                        } else {
                            "listbox"
                        }
                    }
                    "Btn" => {
                        if ff & (1 << 16) != 0 {
                            "button"
                        } else if ff & (1 << 15) != 0 {
                            "radio"
                        } else {
                            "checkbox"
                        }
                    }
                    _ => "unknown",
                };
                let value = v.and_then(|obj| match &obj {
                    Object::Name(n) => Some(String::from_utf8_lossy(n).into_owned()),
                    Object::String(..) => decode_text_string(&obj).ok(),
                    Object::Reference(id) => self
                        .doc
                        .get_object(*id)
                        .ok()
                        .and_then(|o| decode_text_string(o).ok()),
                    Object::Array(items) => Some(
                        items
                            .iter()
                            .filter_map(|i| decode_text_string(i).ok())
                            .collect::<Vec<_>>()
                            .join(", "),
                    ),
                    _ => None,
                });
                (name, kind.to_owned(), value)
            })
            .collect()
    }

    /// チェックボックス / ラジオの取り得る状態名（ウィジェットの AP /N のキー）を返す。
    fn form_button_states(&self, name: &str) -> PyResult<Vec<String>> {
        let (field_id, ft) = self
            .collect_form_fields()
            .into_iter()
            .find(|(n, ..)| n == name)
            .map(|(_, id, ft, ..)| (id, ft))
            .ok_or_else(|| {
                PdfError::new_err(format!("フォームフィールドが見つかりません: {name:?}"))
            })?;
        if ft != "Btn" {
            return Ok(Vec::new());
        }
        let mut states = Vec::new();
        for widget_id in self.field_widgets(field_id) {
            let Ok(widget) = self.doc.get_object(widget_id).and_then(Object::as_dict) else {
                continue;
            };
            let Some(ap) = widget
                .get(b"AP")
                .ok()
                .and_then(|o| deref_dict(&self.doc, o))
            else {
                continue;
            };
            let Some(normal) = ap.get(b"N").ok().and_then(|o| deref_dict(&self.doc, o)) else {
                continue;
            };
            for (key, _) in normal.iter() {
                let state = String::from_utf8_lossy(key).into_owned();
                if !states.contains(&state) {
                    states.push(state);
                }
            }
        }
        Ok(states)
    }

    /// フォームフィールドに値を設定する（NeedAppearances も立てる）。
    ///
    /// Tx / Ch はテキスト文字列、Btn は状態名（Name）として書き込み、
    /// Btn は各ウィジェットの /AS も合わせて更新する（AP に無い状態は Off へ）。
    fn set_form_field(&mut self, name: &str, value: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (field_id, ft) = self
            .collect_form_fields()
            .into_iter()
            .find(|(n, ..)| n == name)
            .map(|(_, id, ft, ..)| (id, ft))
            .ok_or_else(|| {
                PdfError::new_err(format!("フォームフィールドが見つかりません: {name:?}"))
            })?;
        match ft.as_str() {
            "Tx" | "Ch" => {
                let dict = self
                    .doc
                    .get_object_mut(field_id)
                    .and_then(Object::as_dict_mut)
                    .map_err(to_py_err)?;
                dict.set("V", text_string(value));
            }
            "Btn" => {
                {
                    let dict = self
                        .doc
                        .get_object_mut(field_id)
                        .and_then(Object::as_dict_mut)
                        .map_err(to_py_err)?;
                    dict.set("V", Object::Name(value.as_bytes().to_vec()));
                }
                // 各ウィジェットの表示状態（/AS）を V に合わせる（AP に無い状態は Off）
                for widget_id in self.field_widgets(field_id) {
                    let has_state = self
                        .doc
                        .get_object(widget_id)
                        .and_then(Object::as_dict)
                        .ok()
                        .and_then(|w| w.get(b"AP").ok().and_then(|o| deref_dict(&self.doc, o)))
                        .and_then(|ap| ap.get(b"N").ok().and_then(|o| deref_dict(&self.doc, o)))
                        .is_some_and(|normal| normal.has(value.as_bytes()));
                    let state = if has_state { value } else { "Off" };
                    if let Ok(widget) = self
                        .doc
                        .get_object_mut(widget_id)
                        .and_then(Object::as_dict_mut)
                    {
                        widget.set("AS", Object::Name(state.as_bytes().to_vec()));
                    }
                }
            }
            "Sig" => {
                return Err(PdfError::new_err(
                    "署名フィールドへの記入は未対応です（電子署名は pyHanko 連携を参照）",
                ));
            }
            other => {
                return Err(PdfError::new_err(format!(
                    "未対応のフィールド型です: {other:?}"
                )));
            }
        }
        self.set_need_appearances()
    }

    /// 添付ファイル名の一覧を返す（名前ツリーの順序に依らずソート済み）。
    fn embfile_names(&mut self) -> Vec<String> {
        let mut names: Vec<String> = self
            .collect_embedded_files()
            .into_iter()
            .map(|(n, _)| n)
            .collect();
        names.sort();
        names
    }

    /// 添付ファイルの中身を取り出す。
    fn embfile_get(&mut self, name: &str) -> PyResult<Vec<u8>> {
        let entries = self.collect_embedded_files();
        let (_, filespec_id) = entries
            .into_iter()
            .find(|(n, _)| n == name)
            .ok_or_else(|| PdfError::new_err(format!("添付ファイルが見つかりません: {name:?}")))?;
        let filespec = self
            .doc
            .get_object(filespec_id)
            .and_then(Object::as_dict)
            .map_err(to_py_err)?;
        let ef = match filespec.get(b"EF").map_err(to_py_err)? {
            Object::Reference(id) => self
                .doc
                .get_object(*id)
                .and_then(Object::as_dict)
                .map_err(to_py_err)?,
            Object::Dictionary(d) => d,
            _ => return Err(PdfError::new_err("添付ファイルの EF 辞書が壊れています")),
        };
        let stream_ref = ef
            .get(b"F")
            .or_else(|_| ef.get(b"UF"))
            .and_then(Object::as_reference)
            .map_err(to_py_err)?;
        let stream = self
            .doc
            .get_object(stream_ref)
            .and_then(Object::as_stream)
            .map_err(to_py_err)?;
        Ok(stream
            .decompressed_content()
            .unwrap_or_else(|_| stream.content.clone()))
    }

    /// 添付ファイルを追加する（同名が既にあればエラー）。
    fn embfile_add(
        &mut self,
        py: Python<'_>,
        name: String,
        data: Vec<u8>,
        filename: Option<String>,
        desc: Option<String>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        py.detach(|| {
            let entries = self.collect_embedded_files();
            if entries.iter().any(|(n, _)| *n == name) {
                return Err(PdfError::new_err(format!(
                    "同名の添付ファイルが既にあります: {name:?}（先に embfile_del すること）"
                )));
            }
            let size = i64::try_from(data.len()).map_err(|e| PdfError::new_err(e.to_string()))?;
            // 保存時の deflate= で圧縮できるよう、圧縮許可は既定のままにする
            let ef_id = self.doc.add_object(Stream::new(
                dictionary! {
                    "Type" => "EmbeddedFile",
                    "Params" => dictionary! { "Size" => size },
                },
                data,
            ));
            let fname = filename.unwrap_or_else(|| name.clone());
            let mut filespec = dictionary! {
                "Type" => "Filespec",
                "F" => Object::string_literal(fname.clone()),
                "UF" => text_string(&fname),
                "EF" => dictionary! { "F" => ef_id, "UF" => ef_id },
            };
            if let Some(text) = desc {
                filespec.set("Desc", text_string(&text));
            }
            let filespec_id = self.doc.add_object(filespec);
            let mut entries = entries;
            entries.push((name, filespec_id));
            self.write_embedded_files(entries)
        })
    }

    /// 添付ファイルを削除する（無ければエラー）。
    fn embfile_del(&mut self, name: &str) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let entries = self.collect_embedded_files();
        let before = entries.len();
        let remaining: Vec<(String, ObjectId)> =
            entries.into_iter().filter(|(n, _)| n != name).collect();
        if remaining.len() == before {
            return Err(PdfError::new_err(format!(
                "添付ファイルが見つかりません: {name:?}"
            )));
        }
        self.write_embedded_files(remaining)
    }

    /// OCR 結果（表示座標の語 + テキスト）を不可視テキスト層として書き込む。
    ///
    /// フォント実体は埋め込まず、Identity-H + ToUnicode の CID フォントと
    /// Tr 3（不可視）で Unicode と位置だけを持たせる。抽出・検索にだけ現れる。
    fn insert_ocr_layer(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        words: Vec<(f64, f64, f64, f64, String)>,
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let cid_map = ocr::assign_cids(&words);
            if cid_map.len() >= usize::from(u16::MAX) {
                return Err(PdfError::new_err(
                    "OCR 層の文字種が多すぎます（1 回の呼び出しで 65,534 種まで）",
                ));
            }
            let font_id = ocr::add_ocr_font(&mut self.doc, &cid_map);
            self.bake_page_attrs(page_id)?;
            self.doc
                .get_or_create_resources(page_id)
                .map_err(to_py_err)?;
            let name = format!("PyloF{}", font_id.0);
            draw::add_page_font(&mut self.doc, page_id, &name, font_id).map_err(to_py_err)?;
            let ops = ocr::ocr_ops(crop, rotation, &words, &cid_map, &name);
            draw::push_content(&mut self.doc, page_id, ops, true).map_err(to_py_err)
        })
    }

    /// 指定ページ（1 始まり）の表示座標 point をベースライン起点にテキストを印字する。
    ///
    /// lines は WinAnsi エンコード済みのバイト列（1 要素 = 1 行。検証と cp1252 変換は
    /// Python 側）。base_font は標準 14 フォントの正式名。フォントは埋め込まない。
    // Python 側シグネチャをそのまま写す境界メソッドのため引数数は許容する
    #[allow(clippy::too_many_arguments)]
    fn insert_page_text(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        point: (f64, f64),
        lines: Vec<Vec<u8>>,
        base_font: &str,
        winansi: bool,
        fontsize: f64,
        color: (f64, f64, f64),
    ) -> PyResult<()> {
        self.invalidate_hayro_pdf();
        let (crop, rotation) = self.page_display_geometry(page_number)?;
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            let mut font_dict = dictionary! {
                "Type" => "Font",
                "Subtype" => "Type1",
                "BaseFont" => Object::Name(base_font.as_bytes().to_vec()),
            };
            if winansi {
                font_dict.set("Encoding", Object::Name(b"WinAnsiEncoding".to_vec()));
            }
            let font_id = self.doc.add_object(font_dict);
            self.bake_page_attrs(page_id)?;
            self.doc
                .get_or_create_resources(page_id)
                .map_err(to_py_err)?;
            let name = format!("PyloF{}", font_id.0);
            draw::add_page_font(&mut self.doc, page_id, &name, font_id).map_err(to_py_err)?;
            let ops = draw::text_ops(crop, rotation, point, &lines, &name, fontsize, color);
            draw::push_content(&mut self.doc, page_id, ops, true).map_err(to_py_err)
        })
    }

    /// 指定ページ（1 始まり）のテキストを置換し、置換回数を返す。
    ///
    /// lopdf の replace_partial_text をそのまま公開する薄い層。単純エンコーディングの
    /// フォントだけが対象で、コンテンツは lopdf の content パーサを往復する
    /// （制約の説明は Python 側 docstring が担う）。
    fn replace_text_on_page(
        &mut self,
        py: Python<'_>,
        page_number: u32,
        search: &str,
        replacement: &str,
        default_char: Option<String>,
    ) -> PyResult<usize> {
        self.invalidate_hayro_pdf();
        let page_id = self.page_id(page_number)?;
        py.detach(|| {
            // lopdf の get_page_fonts はページ属性の継承を解決しないため、先に焼き込む
            self.bake_page_attrs(page_id)?;
            self.doc
                .replace_partial_text(page_number, search, replacement, default_char.as_deref())
                .map_err(|e| lopdf_err(Some("テキスト置換に失敗しました"), &e))
        })
    }
}
