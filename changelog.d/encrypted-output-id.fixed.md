An encrypted file saved from a document without a file identifier gains a
random `/ID`, which ISO 32000-2 requires whenever `/Encrypt` is present; qpdf
reported AES-256 output without one as invalid.
