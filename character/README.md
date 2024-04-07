# character.shpk

## Material Keys

### Vertex Color Mode (`0xF52CCF05`)

This key is ignored if the Texture Mode is set to Simple.

- Color (`…Color`, `0xDFE74BAC`): defines `VERTEX_COLOR`
- Multi (`…Mask`, `0xA7D2FF60`): defines `VERTEX_MASK`

### Texture Mode (`GetValues`, `0xB616DC5A`)

- Multi (`0x5CC605B5`): defines `MODE_DEFAULT`
- Simple (`…Simple`, `0x22A4AABF`): defines `MODE_SIMPLE`
- Diffuse / Specular (`…Compatibility`, `0x600EF9DF`): defines `MODE_COMPATIBILITY`

### Decal Mode (`GetDecalColor`, `0xD2777173`)

This key is ignored if the Texture Mode is set to Simple.

- None (`…Off`, `0x4242B842`): defines `DECAL_OFF`
- Alpha (`…Alpha`, `0x584265DD`): defines `DECAL_ALPHA`
- Color (`…RGBA`, `0xF35F5131`): defines `DECAL_COLOR`

### Specular Map Mode (`0xC8BD1DEF`)

This key only applies if the Texture Mode is set to Diffuse / Specular.

- Color (`…ReflectivityRGB`, `0x198D11CD`): defines `COMPAT_DEFAULT`
- Multi (`…Mask`, `0xA02F4828`): defines `COMPAT_MASK`
