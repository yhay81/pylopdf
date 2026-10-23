`LimitError` no longer reports `recovery_scan_size`. lopdf 0.46 rebuilds a
damaged cross-reference table in linear time however many unterminated
`stream` keywords the input holds, so pylopdf no longer scans lenient input
beforehand and refuses none of it; 64 MiB of such keywords rebuild in about
0.25 s.
