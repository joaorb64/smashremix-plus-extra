# Editable ROM / GE model workflow

1. **Generate the editable ROM**
   - `python3 appender/scripts/gen_editable_rom_extra.py --character <NAME> --rom ssb64asm_extra.z64`
   - Copy the output `editable_rom.z64` into the character's `editable/` folder.
   - Note the printed `SetModelForm` commands (bone/form pairs) for later.

2. **In GE**
   - Load the editable ROM, open the character.
   - Add the `SetModelForm` commands from step 1 for any special parts.
   - Export: **Export Model Obj** (writes `.fbx`, despite the name), **Export Special Mapping Reference**, **Parse DisplayLists to Text File**.

3. **Generate texture manifests**
   - `python3 appender/scripts/gen_texture_manifest.py <editable_folder>`
   - Writes `textures.txt` / `specialtextures.txt`.
   - For a Special Part export, add `--scope "<Special Part>.fbx"` to scope the manifest to just that part's textures.

4. **Fix the FBX**
   - `python3 appender/scripts/apply_material_flags_to_fbx.py <character.bin> "<exported>.fbx" --displaylist "<DisplayLists>.txt"`
   - This one pass: renames special-part meshes to `RoomXX` (by bound bone), tags materials (`Transparent`/`ClampS`/`ClampT`/`MirrorS`/`MirrorT`/`CullBoth`), tags rooms with `LightColor.../ShadowColor...`, splits rooms with mixed light/shadow colors into `RoomXX_1_.../RoomXX_2_...`, and strips the unused `DefaultMaterial`.
   - Writes `<name>_fixed.fbx`.
