`doc.layers.configurations` lists the optional-content configurations as
`LayerConfiguration` handles with assignable `name`, `creator`, `base_state`,
`on`, `off`, `locked`, `radio_groups`, and `order` (a tree of `Layer` handles
and `LayerOrder` values); `apply()` makes one the default that rendering and
extraction use, and `configurations.add()` adds one.
