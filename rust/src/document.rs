//! lopdf::Document の Python バインディング。
//!
//! 型変換とエラー変換のみを担う薄い層。使いやすい API は Python 側の
//! `pylopdf.Document` が提供する。

use std::collections::BTreeMap;

use hayro::hayro_interpret::InterpreterSettings;
use hayro::hayro_syntax::Pdf;
use hayro::{RenderCache, RenderSettings, render};
use lopdf::{Dictionary, Document, LoadOptions, Object, ObjectId, StringFormat, dictionary};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

/// ページ辞書へ親ツリーから継承され得る属性キー。
const INHERITABLE_PAGE_KEYS: [&[u8]; 4] = [b"Resources", b"MediaBox", b"CropBox", b"Rotate"];

/// lopdf のエラーを Python の ValueError に変換する。
fn to_py_err(e: lopdf::Error) -> PyErr {
    PyValueError::new_err(e.to_string())
}

/// PDF テキスト文字列をデコードする。
///
/// UTF-16BE（BOM 付き）→ UTF-8 → Latin-1（PDFDocEncoding の近似）の順で解釈する。
fn decode_pdf_string(bytes: &[u8]) -> String {
    if let Some(body) = bytes.strip_prefix(&[0xFE, 0xFF]) {
        let units: Vec<u16> = body
            .chunks_exact(2)
            .map(|pair| u16::from_be_bytes([pair[0], pair[1]]))
            .collect();
        String::from_utf16_lossy(&units)
    } else if let Ok(s) = std::str::from_utf8(bytes) {
        s.to_owned()
    } else {
        bytes.iter().map(|&b| b as char).collect()
    }
}

/// テキストを PDF 文字列オブジェクトにエンコードする（ASCII 外は UTF-16BE）。
fn encode_pdf_string(text: &str) -> Object {
    if text.is_ascii() {
        Object::string_literal(text)
    } else {
        let mut bytes = vec![0xFE, 0xFF];
        for unit in text.encode_utf16() {
            bytes.extend_from_slice(&unit.to_be_bytes());
        }
        Object::String(bytes, StringFormat::Hexadecimal)
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
        while let Some(parent_id) = parent {
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

/// lopdf::Document を保持する Python クラス。
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct _Document(pub Document);

impl _Document {
    /// trailer の Info 辞書を（間接参照を解決して）返す。
    fn info_dict(&self) -> Option<&Dictionary> {
        match self.0.trailer.get(b"Info").ok()? {
            Object::Reference(id) => self.0.get_object(*id).ok()?.as_dict().ok(),
            Object::Dictionary(dict) => Some(dict),
            _ => None,
        }
    }

    /// 現在の編集状態をシリアライズしたバイト列を返す（レンダリング用）。
    fn current_bytes(&mut self) -> PyResult<Vec<u8>> {
        let mut buffer = Vec::new();
        self.0
            .save_to(&mut buffer)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(buffer)
    }

    /// 現在の編集状態を hayro のドキュメントとして開き直す。
    fn build_hayro_pdf(&mut self) -> PyResult<Pdf> {
        let data = self.current_bytes()?;
        Pdf::new(data)
            .map_err(|e| PyValueError::new_err(format!("failed to parse PDF for rendering: {e:?}")))
    }

    /// ルート Pages ノードの ObjectId を返す。無ければ最小構造を作る（空ドキュメント対応）。
    fn ensure_page_tree(&mut self) -> lopdf::Result<ObjectId> {
        let existing = self
            .0
            .catalog()
            .and_then(|catalog| catalog.get(b"Pages"))
            .and_then(Object::as_reference);
        if let Ok(pages_id) = existing {
            return Ok(pages_id);
        }
        let pages_id = self.0.add_object(dictionary! {
            "Type" => "Pages",
            "Kids" => Vec::<Object>::new(),
            "Count" => 0,
        });
        let catalog_id = self.0.add_object(dictionary! {
            "Type" => "Catalog",
            "Pages" => pages_id,
        });
        self.0.trailer.set("Root", catalog_id);
        Ok(pages_id)
    }
}

#[pymethods]
impl _Document {
    /// 空の PDF ドキュメントを作る。
    #[new]
    fn new() -> Self {
        Self(Document::with_version("1.7"))
    }

    /// ファイルパスから読み込む。
    #[staticmethod]
    fn load(path: &str) -> PyResult<Self> {
        Document::load(path)
            .map(Self)
            .map_err(|e| PyValueError::new_err(format!("failed to load {path}: {e}")))
    }

    /// バイト列から読み込む。
    #[staticmethod]
    fn load_bytes(data: &[u8]) -> PyResult<Self> {
        Document::load_mem(data).map(Self).map_err(to_py_err)
    }

    /// パスワード付きでファイルパスから読み込む（ロード時に復号する）。
    #[staticmethod]
    fn load_with_password(path: &str, password: &str) -> PyResult<Self> {
        Document::load_with_options(path, LoadOptions::with_password(password))
            .map(Self)
            .map_err(|e| PyValueError::new_err(format!("failed to load {path}: {e}")))
    }

    /// パスワード付きでバイト列から読み込む（ロード時に復号する）。
    #[staticmethod]
    fn load_bytes_with_password(data: &[u8], password: &str) -> PyResult<Self> {
        Document::load_mem_with_options(data, LoadOptions::with_password(password))
            .map(Self)
            .map_err(to_py_err)
    }

    /// 現在も暗号化されたままか（復号済みなら false）。
    fn is_encrypted(&self) -> bool {
        self.0.is_encrypted()
    }

    /// ロード時点で暗号化されていたか（復号後も true のまま）。
    fn was_encrypted(&self) -> bool {
        self.0.was_encrypted()
    }

    /// user password として正しいか（復号はしない）。
    fn authenticate_user_password(&self, password: &str) -> bool {
        self.0.authenticate_user_password(password).is_ok()
    }

    /// owner password として正しいか（復号はしない）。
    fn authenticate_owner_password(&self, password: &str) -> bool {
        self.0.authenticate_owner_password(password).is_ok()
    }

    /// ファイルパスへ保存する。
    fn save(&mut self, path: &str) -> PyResult<()> {
        self.0
            .save(path)
            .map(|_| ())
            .map_err(|e| PyValueError::new_err(format!("failed to save {path}: {e}")))
    }

    /// バイト列へ書き出す。
    fn save_bytes(&mut self) -> PyResult<Vec<u8>> {
        let mut buffer = Vec::new();
        self.0
            .save_to(&mut buffer)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        Ok(buffer)
    }

    /// ページ数を返す。
    fn page_count(&self) -> usize {
        self.0.get_pages().len()
    }

    /// PDF バージョン文字列（例: "1.7"）を返す。
    fn version(&self) -> String {
        self.0.version.clone()
    }

    /// Info 辞書の文字列項目を {キー: 値} で返す。
    fn get_metadata(&self) -> BTreeMap<String, String> {
        let mut result = BTreeMap::new();
        let Some(info) = self.info_dict() else {
            return result;
        };
        for (key, value) in info.iter() {
            let resolved = match value {
                Object::Reference(id) => match self.0.get_object(*id) {
                    Ok(object) => object,
                    Err(_) => continue,
                },
                other => other,
            };
            if let Ok(bytes) = resolved.as_str() {
                result.insert(
                    String::from_utf8_lossy(key).into_owned(),
                    decode_pdf_string(bytes),
                );
            }
        }
        result
    }

    /// Info 辞書の項目を設定する。値が空文字列なら項目を削除する。
    fn set_metadata(&mut self, key: &str, value: &str) -> PyResult<()> {
        let info_id = if let Ok(Object::Reference(id)) = self.0.trailer.get(b"Info") {
            *id
        } else {
            // 直置き辞書は間接オブジェクトへ移し、無ければ新規作成する
            let existing = match self.0.trailer.get(b"Info") {
                Ok(Object::Dictionary(dict)) => dict.clone(),
                _ => Dictionary::new(),
            };
            let id = self.0.add_object(existing);
            self.0.trailer.set("Info", id);
            id
        };
        let info = self
            .0
            .get_object_mut(info_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        if value.is_empty() {
            info.remove(key.as_bytes());
        } else {
            info.set(key, encode_pdf_string(value));
        }
        Ok(())
    }

    /// 指定ページ（1 始まり）を削除する。
    fn delete_pages(&mut self, page_numbers: Vec<u32>) {
        self.0.delete_pages(&page_numbers);
    }

    /// 指定ページ（1 始まり）のテキストを抽出する。
    ///
    /// lopdf の extract_text はページ属性の継承（親 Pages 側の Resources 等)を
    /// 解決しないため、先に対象ページへ継承属性を焼き込んでから抽出する。
    fn extract_text(&mut self, page_numbers: Vec<u32>) -> PyResult<String> {
        let pages = self.0.get_pages();
        for number in &page_numbers {
            if let Some(&page_id) = pages.get(number) {
                let dict = resolve_inherited_page_dict(&self.0, page_id).map_err(to_py_err)?;
                self.0.set_object(page_id, dict);
            }
        }
        self.0.extract_text(&page_numbers).map_err(to_py_err)
    }

    /// 別ドキュメントの全ページを末尾に取り込む。
    fn merge(&mut self, other: &Self) -> PyResult<()> {
        let mut other_doc = other.0.clone();
        other_doc.renumber_objects_with(self.0.max_id + 1);
        let new_max_id = other_doc.max_id;

        let other_page_ids: Vec<ObjectId> = other_doc.get_pages().into_values().collect();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;

        // 取り込み元のページツリーは捨てるため、継承属性を各ページへ焼き込む
        let mut resolved_pages = Vec::with_capacity(other_page_ids.len());
        for &page_id in &other_page_ids {
            let mut dict = resolve_inherited_page_dict(&other_doc, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            resolved_pages.push((page_id, dict));
        }

        // ページツリー構造（Catalog / Pages / Page）以外のオブジェクトを取り込む
        for (id, object) in other_doc.objects {
            match object.type_name().unwrap_or(b"") {
                b"Catalog" | b"Pages" | b"Page" => {}
                _ => {
                    self.0.objects.insert(id, object);
                }
            }
        }
        for (id, dict) in resolved_pages {
            self.0.objects.insert(id, Object::Dictionary(dict));
        }

        // ルート Pages の Kids / Count を更新する
        let added = i64::try_from(other_page_ids.len())
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let pages_dict = self
            .0
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        let old_count = pages_dict
            .get(b"Count")
            .and_then(Object::as_i64)
            .unwrap_or(0);
        let mut kids = match pages_dict.get(b"Kids").and_then(Object::as_array) {
            Ok(kids) => kids.clone(),
            Err(_) => Vec::new(),
        };
        kids.extend(other_page_ids.into_iter().map(Object::Reference));
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", old_count + added);

        self.0.max_id = new_max_id;
        Ok(())
    }

    /// 指定ページ（1 始まり）だけを指定順で残す。並べ替えにも使える。
    ///
    /// PDF のページツリーでは Parent が一意である必要があるため、
    /// 同一ページの重複指定（複製）は未対応。
    fn select(&mut self, page_numbers: Vec<u32>) -> PyResult<()> {
        let mut seen = std::collections::HashSet::new();
        for number in &page_numbers {
            if !seen.insert(*number) {
                return Err(PyValueError::new_err(format!(
                    "ページ {number} が重複しています（複製は未対応）"
                )));
            }
        }

        let pages = self.0.get_pages();
        let pages_id = self.ensure_page_tree().map_err(to_py_err)?;

        // 中間 Pages ノードは捨てて平坦化するため、継承属性を焼き込み Parent を付け替える
        let mut selected = Vec::with_capacity(page_numbers.len());
        for number in &page_numbers {
            let page_id = *pages
                .get(number)
                .ok_or_else(|| PyValueError::new_err(format!("ページ {number} は存在しません")))?;
            let mut dict = resolve_inherited_page_dict(&self.0, page_id).map_err(to_py_err)?;
            dict.set("Parent", pages_id);
            selected.push((page_id, dict));
        }

        let kids: Vec<Object> = selected
            .iter()
            .map(|(id, _)| Object::Reference(*id))
            .collect();
        let count = i64::try_from(kids.len()).map_err(|e| PyValueError::new_err(e.to_string()))?;
        for (id, dict) in selected {
            self.0.objects.insert(id, Object::Dictionary(dict));
        }
        let pages_dict = self
            .0
            .get_object_mut(pages_id)
            .and_then(Object::as_dict_mut)
            .map_err(to_py_err)?;
        pages_dict.set("Kids", kids);
        pages_dict.set("Count", count);

        // 参照されなくなったページ・中間ノードを掃除する
        self.0.prune_objects();
        Ok(())
    }

    /// 指定ページ（1 始まり）を PNG 画像にレンダリングする。
    fn render_page_png(&mut self, page_number: u32, scale: f32) -> PyResult<Vec<u8>> {
        if scale <= 0.0 {
            return Err(PyValueError::new_err("scale は正の値で指定してください"));
        }
        let pdf = self.build_hayro_pdf()?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PyValueError::new_err(format!("ページ {page_number} は存在しません")))?;
        let interpreter_settings = InterpreterSettings::default();
        let render_settings = RenderSettings {
            x_scale: scale,
            y_scale: scale,
            ..Default::default()
        };
        let cache = RenderCache::new();
        let pixmap = render(page, &cache, &interpreter_settings, &render_settings);
        pixmap
            .into_png()
            .map_err(|e| PyValueError::new_err(format!("failed to encode PNG: {e:?}")))
    }

    /// 指定ページ（1 始まり）を SVG 文字列にレンダリングする。
    fn render_page_svg(&mut self, page_number: u32) -> PyResult<String> {
        let pdf = self.build_hayro_pdf()?;
        let pages = pdf.pages();
        let page = page_number
            .checked_sub(1)
            .and_then(|index| pages.get(index as usize))
            .ok_or_else(|| PyValueError::new_err(format!("ページ {page_number} は存在しません")))?;
        let interpreter_settings = InterpreterSettings::default();
        let cache = hayro_svg::RenderCache::new();
        let settings = hayro_svg::SvgRenderSettings::default();
        Ok(hayro_svg::convert(
            page,
            &cache,
            &interpreter_settings,
            &settings,
        ))
    }

    /// ストリームを圧縮する。
    fn compress(&mut self) {
        self.0.compress();
    }

    /// ストリームを展開する。
    fn decompress(&mut self) {
        self.0.decompress();
    }

    /// 参照されていないオブジェクトを削除する。
    fn prune_objects(&mut self) {
        self.0.prune_objects();
    }
}
