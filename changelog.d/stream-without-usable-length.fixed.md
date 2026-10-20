Editing and saving keep the data of a stream whose `/Length` is missing or
unusable. lopdf loaded a stream whose `/Length` is missing, is not an
integer, or names an object without one with no data, or took as many bytes
as an integral real declared without looking for `endstream`, while
rendering and extraction read the original bytes through hayro, which reads
such a stream up to `endstream`. Any edit or save therefore dropped content
that pylopdf had just shown: pdf.js `issue1293r.pdf` extracted `Issue 1293`
before `tobytes()` and nothing after. Loading now stores the data that hayro
reads, with a direct `/Length`, and renders from the serialized graph when
hayro would read the stream past its object or not at all.
