//! レンダリング結果のピクセルマップ（ストレートアルファの RGBA8）。
//!
//! `np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, 4)`
//! のように NumPy / PIL から利用できる。
//!
//! 注意: buffer protocol（ゼロコピー）は実装していない。`Py_buffer` が
//! Python の安定 ABI に入ったのは 3.11 からで、abi3-py310 ビルドでは
//! 使えないため（samples は 1 回のコピーになる）。abi3 の下限を 3.11 へ
//! 上げるか cp314t 別ビルドを行う際に再検討する。

use pyo3::prelude::*;

use crate::document::PdfError;

/// レンダリング済みページのピクセルマップ。
///
/// データはストレート（非プリマルチプライド）アルファの RGBA8、行優先。
#[pyclass(module = "pylopdf.pylopdf_core")]
pub struct Pixmap {
    pub(crate) width: u32,
    pub(crate) height: u32,
    /// RGBA8（straight alpha）の行優先データ。
    pub(crate) data: Vec<u8>,
}

#[pymethods]
impl Pixmap {
    /// 幅（ピクセル）。
    #[getter]
    fn width(&self) -> u32 {
        self.width
    }

    /// 高さ（ピクセル）。
    #[getter]
    fn height(&self) -> u32 {
        self.height
    }

    /// 1 ピクセルあたりの成分数（常に 4 = RGBA）。
    #[getter]
    fn n(&self) -> u32 {
        4
    }

    /// 1 行あたりのバイト数（幅 × 4）。
    #[getter]
    fn stride(&self) -> u32 {
        self.width * 4
    }

    /// ピクセルデータ（RGBA8・行優先）を bytes で返す。
    #[getter]
    fn samples<'py>(&self, py: Python<'py>) -> Bound<'py, pyo3::types::PyBytes> {
        pyo3::types::PyBytes::new(py, &self.data)
    }

    /// PNG バイト列にエンコードして返す。
    fn tobytes(&self) -> PyResult<Vec<u8>> {
        crate::extract::encode_png(self.width, self.height, png::ColorType::Rgba, &self.data)
            .ok_or_else(|| PdfError::new_err("failed to encode PNG"))
    }

    fn __repr__(&self) -> String {
        format!("<Pixmap {}x{} rgba>", self.width, self.height)
    }
}
