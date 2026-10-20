"""Attach a CSV file, list it, replace its data, and remove it again."""

import fitz

doc = fitz.open()
doc.new_page()

doc.embfile_add("data.csv", b"a,b\n1,2\n", filename="data.csv", desc="Measurements")
assert doc.embfile_count() == 1
assert doc.embfile_names() == ["data.csv"]
assert doc.embfile_get("data.csv") == b"a,b\n1,2\n"

doc.embfile_upd("data.csv", b"a,b\n3,4\n")
saved = fitz.open(stream=doc.tobytes())
assert saved.embfile_get("data.csv") == b"a,b\n3,4\n"

saved.embfile_del("data.csv")
assert saved.embfile_count() == 0
print("attachments ok")
