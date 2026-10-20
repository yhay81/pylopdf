`Document.save` and `Pixmap.save` preserve an existing Windows file's
discretionary access-control list and inheritance protection when replacing
it. Access restrictions are copied to the temporary output before writing
data; failure to copy them refuses the save and preserves the original.
