`Document.save` supports Windows paths longer than `MAX_PATH` without
requiring a machine-wide policy change or an explicit extended-length path
prefix. Relative paths, Unicode names, and existing output files use the
same handling in normal, object-stream, and encrypted saves.
