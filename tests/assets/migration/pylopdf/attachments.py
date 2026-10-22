"""Attach a CSV file, list it, replace its data, and remove it again."""

import pylopdf

doc = pylopdf.Document()
doc.pages.new()

doc.attachments.add("data.csv", b"a,b\n1,2\n", filename="data.csv", description="Measurements")
assert len(doc.attachments) == 1
assert list(doc.attachments) == ["data.csv"]
assert doc.attachments["data.csv"].read() == b"a,b\n1,2\n"

doc.attachments["data.csv"].write(b"a,b\n3,4\n")
saved = pylopdf.open(doc.tobytes())
assert saved.attachments["data.csv"].read() == b"a,b\n3,4\n"

del saved.attachments["data.csv"]
assert len(saved.attachments) == 0
print("attachments ok")
