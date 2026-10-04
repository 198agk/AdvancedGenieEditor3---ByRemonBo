This tool lets you convert SLP and PSD files into SMP/SMX/SLD files.
For SLP to SMX conversion, refer to SLP/readme.txt.

=== PSD to SLD conversion ===
Under one of the needed folders (buildings / nature / units), create a subfolder consisting of your PSD frames in filename_0000.psd format. Example: n_tree_bamboo_0000.psd, n_tree_bamboo_0001.psd...
Each PSD requires the following layers:
- Damage (for nature/buildings, clipping mask of Diffuse) OR Blood (for units, clipping mask of Diffuse)
- AmbientOcclusion (clipping mask of Diffuse)
- Diffuse (main layer, masked with white-on-black bitmap)
- Decal
- Background
- Height
- Normals
Then either run convert_psd.bat or drag and drop your specific folder(s) onto DESpriteTool.exe to begin the SLD conversion process.

settings.json files contain the conversion settings. Each subfolder containing an additional settings.json will amend the upper-level set of settings.
It does not matter how deep the subfolder with graphics is - however the contents of any folder starting with an underscore will be ignored.
