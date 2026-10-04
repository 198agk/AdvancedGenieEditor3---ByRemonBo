Advanced Genie Editor

This software is licensed using GNU General Public License Version 3 (GPLv3). This means that you are free to modify, (re)use, and (re)distribute the software as long as the license and copyright notices are preserved, and the full source code of your edition is made available. A copy of the GPLv3 license text can be found in the "AGE_License.txt" file in your distribution. The copyright notice for the software can be found in the source code.

For your convenience, a binary copy of the software is included with your distribution.

In accordance with the terms of GPLv3, the source code of this software is made available to you at the following location(s):
- "Source.7z\AGE" in your distribution

The latest version of this software is available at the following URL(s):
- https://github.com/Tapsa/AGE

Below is the ReadMe, Release Notes, and Credits for this software.

-------------------------------------------------------------

# Advanced Genie Editor

A program for editing data files of Age of Empires, Age of Empires II, Star Wars: Galactic Battlegrounds, their expansions, upgrades, and mods.

## Building

To compile with MinGW you need to download `wxWidgets`, `Boost`, and `CMake`.

For Visual Studio, you also need to download and build a compatible version of `libiconv`.

`SFML` is used for simultaneous audio playback.

`lz4` is used for decompressing some sprites.

### CMake 3.29.2

Install normally and select it to update PATH automatically.
You may need to edit version numbers in

```sh
\CMake\share\cmake-3.29\Modules\Find*.cmake
```

files to find `wxWidgets` and `Boost`.

Extract rest of the stuff into a folder of your choice.

For Visual Studio, add a folder as a system variable `%Cpp libs%`,
where you'll put all required headers and built libraries.

### Mingw-builds 8.1.0

Includes `gdb`, `libiconf`, `zlib`.

Choose `posix threads with dwarf exception handling`.

After installing MinGW add its `bin` folder to the system path.

### wxWidgets 3.2.4

- Unpack the zip file.

- Open the commandline and change the working directory to `\wxWidgets\build\msw`

- Execute:

    ```sh
    mingw32-make -f makefile.gcc BUILD=debug clean

    mingw32-make -f makefile.gcc BUILD=debug SHARED=1

    mingw32-make -f makefile.gcc BUILD=release clean

    mingw32-make -f makefile.gcc BUILD=release SHARED=1

    del /s *.o

    del /s *.o.d
    ```

For Visual Studio, open the solution for your Visual Studio version or nearest one in `\wxWidgets\build\msw` and build all of it using all configurations you're planning to build **Advanced Genie Editor** with.

### Boost 1.85.0

- Unpack the zip file.

- Open the commandline and change the working directory to `\boost_1_85_0\tools\build`

- Run `bootstrap.bat`

- Copy `b2.exe` to `\boost_1_85_0`

- Run from `\boost_1_85_0`:

    ```sh
    b2 toolset=gcc link=shared runtime-link=shared threading=multi --with-iostreams --with-json
    
    # For Visual Studio, zlib needs to be built too.
    
    b2 toolset=msvc link=static runtime-link=shared threading=multi --with-iostreams --with-json -s ZLIB_SOURCE=".\tools\boost_install\test\iostreams\zlib-1.2.11" -s ZLIB_INCLUDE=".\tools\boost_install\test\iostreams\zlib-1.2.11"
    ```

You can safely delete the `boost_1_85_0` folder after build completes.

Visual Studio may require appending `lib` to name of some generated libraries.

### SFML 2.6.1

- Unpack the zip file.

- Use the `cmake-gui` and browse the `SFML` source folder.

- Configure and ensure both `Debug` and `MinSizeRel` CMAKE_CONFIGURATION_TYPES are present.

- Run from `\SFML`

    ```sh
    mingw32-make install
    ```

- Copy `FindSFML.cmake` to `\CMake\share\cmake-3.15\Modules`

### lz4

- Unpack the zip file.

- For static linking using Visual Studio, build the liblz4 project, both `Debug` and `Release`.

- Copy the header files from `lib` folder into `%Cpp libs%\lz4`.

- Create folders `Debug` and `Release` inside `%Cpp libs%\lz4` folder.

- Copy the generated `liblz4_static.lib` files from where they were generated, such as `build\VS2017\bin`.

### Final steps

Clone the following projects into same folder where the `AGE` folder is:

- [Genieutils](https://github.com/Tapsa/genieutils)

- [pcrio](https://github.com/Tapsa/pcrio)

MinGW: Update paths in `build*.bat` files, and then you are ready to go.

Visual Studio solution should be buildable now, but you may need to add some libraries to system path, or copy them into build folders.

If building shared libs copy necessary DLL files to execution path or add lib paths to system path.

- wx: base, core

- Boost: iostreams

- genieutils (after each change)

## License

**GNU GPLv3**; see [LICENSE](LICENSE).

-------------------------------------------------------------

[b]Advanced Genie Editor[/b]
for Age of Empires, Rise of Rome (including DE), Age of Kings, The Conquerors (including DE), Star Wars Galactic Battlegrounds and Clone Campaigns.

This is a program for editing data of genie (DAT and DLL) files. It can edit properties of units, civilizations, technologies, graphics, terrains, sounds, player colors and some other things.

[b]Please[/b], ask questions on the [url=http://aok.heavengames.com/cgi-bin/forums/display.cgi?action=st&fn=9&tn=44059&st=recent&f=9,44059,0,365]forums[/url].

Check out [url=http://aok.heavengames.com/blacksmith/showfile.php?fileid=11349]this[/url] program also. It's for graphical modding.

For editing language files in The Conquerors,
I recommend [url=http://aok.heavengames.com/blacksmith/showfile.php?fileid=10819]this download[/url].

[b]Version History[/b]
Guide makers et cetera, please include the version into which your guide was written.

Version [b]2026.2.12[/b]:
-Changed num units in tech tree from u8 to int16 to support more than 255 units.

Version [b]2026.1.8[/b]:
-Add a cli argument to request and output latest supported DAT file version. The version is written to a file named "age_dat_version.txt" in the current working directory.

Version [b]2025.12.1[/b]:
-Add support for internal tool triggered when saving the DAT file.

Version [b]2025.6.26[/b]:
-Fixed filters and some UI elements for Research Locations and Train Locations.

Version [b]2025.6.3[/b]:
-Fixed display logic of some editing boxes for AoE 2 DE.

Version [b]2025.5.23[/b]:
-Added configuration for multiple Research Locations for technologies.

Version [b]2025.4.28[/b]:
-Save and read colors of data editing boxes.

Version [b]2025.4.25[/b]:
-Fixed crash on exit.

Version [b]2025.4.23[/b]:
-Graphics: fixed "Import delta change list" action to match with new in game Sprite Delta Editor. Added "Import Deltas" action to automatically load tr_spred.txt file from current working directory. 

Version [b]2025.2.20[/b]:
-Moved unit hotkey variable from Unit.h to TrainLocation.h (as part of Creatable.h) to support different hotkeys for multiple train locations

Version [b]2025.2.14[/b]:
-Updated description for "Upgrade Unit" command type modes
-Added command type 206 - Transform Selected Unit

Version [b]2025.2.13[/b]:
-Added support for multiple Train Locations to Units

Version [b]2025.2.3[/b]:
-Added dropdown and text data for command types 200-205
-Updated description for "Upgrade Unit" command type modes

Version [b]2025.1.17[/b]:
-Added resource field "enabled" in Units -> Tasks

Version [b]2024.7.2[/b]:
-Added unit attribute fields: "Charge Projectile Unit", "Attack Priority", "Invulnerability Level".

Version [b]2024.5.27[/b]:
-Added unit attribute fields: "Button Icon ID", "Short Tooltip ID", "Extended Tooltip ID", "Hotkey Action ID".

Version [b]2024.5.9[/b]:
-Fixed display issues with some technology effect types.
AoE II DE:
-Added missing technology effect types.
-Named more unit task action types.

Version [b]2024.5.2[/b]:
-Registry defaults also work in the 64-bit version.
AoE II DE:
-Added support for more drop sites.
-Named more effect unit attributes.
-Named more player resources.
-Improved resource store mode and hide in editor tooltips.

Version [b]2024.4.18[/b]:
-Added warning when saving into steamapps.

Version [b]2023.5.21[/b]:
-Restored functionality for Star Wars.

Version [b]2023.5.17[/b]:
-Added support for Return of Rome.

Version [b]2023.3.7[/b]:
-Fixed heap corruption when using a language DLL file.

Version [b]2022.8.21[/b]:
-Improved search filters.
-Terrains can use localized name, if available.

Version [b]2022.2.2[/b]:
-Prevent focusing on sub-lists unintentionally.
-Updated default civilization resource names for AoE II DE.
-Allow choosing a file for editable lists.

Version [b]2021.10.24[/b]:
-Added support for updated Expanding Fronts.
-Added missing technology effect types into list for AoE II DE.

Version [b]2021.8.23[/b]:
-Restored redundant and slower, but functioning erasure of multiple items from lists.

Version [b]2021.7.25[/b]:
-Restored modifiers for strings, /+ with text to append, and /- with integer to shorten the strings.

Version [b]2021.7.19[/b]:
-Added hero glow sprite for units.
-Fixed display angle in SLP view.

Version [b]2021.5.9[/b]:
-Updated technology effect attribute list for DE2.
-Updated armor list defaults for DE2.
-Fixed multiple bugs in units and terrain restrictions tabs.
-Added additional folder for loading modded sprite files.
-Prevented AGE windows from spawning beyond observable desktop.
-Fixed fresh bug in SLP view close handler.

Version [b]2021.4.25[/b]:
-Fixed more bugs in effects tab including inability to type in negative armor and attack values for UP and DE2.
-Show all angles of DE sprites.
-Fixed targeting with mouse.

Version [b]2021.4.18[/b]:
-Scroll rate of tabs can be adjusted in options.
-Window no longer spawns larger than screen space.

Version [b]2021.4.11[/b]:
-Age of Empires DE SLP shadows displayed.
-Sprite cache based on memory used instead of sprite count.

Version [b]2021.4.4[/b]:
-Fixed crash when no palettes were loaded.
-Made SLP view update more reliably.
-Smoother scrolling.
-Improvements for high display scaling.

Version [b]2021.4.2[/b]:
-Added SMX reading support.
-Fixed reading Age of Empires DE SLP files.
-DE palette paths are saved in recent list.
-Improved reading of palettes.
-Fixed some rare crashes when switching data files.

Version [b]2021.3.22[/b]:
-Fixed several bugs in effects tab.
-Reduced lag when dragging main window.
-Stopped old recent paths from opening as wrong version.

Version [b]2021.3.15[/b]:
-Updated effects tab for UserPatch 1.5.
-More accurate text input error messages.

Version [b]2021.3.12[/b]:
-Added support for Expanding Fronts 1.3 and beyond.
-Fixed bugs in effect commanmd type drop down menu.

Version [b]2020.10.2[/b]:
-Support for new charge and damage resistance features in AoE II DE.
-Revealed hidden boxes in tech effects for new features in AoE II DE.

Version [b]2020.6.12[/b]:
-Fixed bug with language text indexes.
-Renamed technology cost and time modifiers for AoE DE.

Version [b]2020.3.30[/b]:
-Support for new AoE II DE game features.

[b]Notes[/b]

[color=BA0000]Make your own backups or use the auto-backup feature![/color]
Extract to anywhere and run. No help files are included.
You may need to run the program as administrator if your game is installed in a protected folder.
You may be able to undo changes by going back to the text box and pressing Ctrl + Z.

[b]Tips[/b]

You can have multiple search entries separated with "|" letter.
Upper search boxes are inclusive and lower ones exclusive.
Example: "tower|ship|ram"
Use the check boxes to toggle matching between all and any entries.

[b]Developers[/b]

Mikko "Tapsa" P, since 2.0b
Manuel Winocur, since 2023
Charles Harbord, since 2024
Igor Djordjevic aka BugA_the_Great, since 2024
Apre - genieutils, from 2011 to 2013
Estien Nifo aka StSB77, from 1.0a to 2.0a

[b]Credits[/b]

Ykkrosh - GeniEd 1 source code
Scenario_t_c - GeniEd 2 source code
Alexandra "Taichi San", DarkRain654 - data file research
DiGiT, JustTesting1234, AOHH - genie file structure
Cysion, Kris, Sarthos - important help
Tevious - SMX support
Grenadier (RvA) - terrain patch
withmorten - UserPatch effect support
BF_Tanks - some help
Donnieboy, Sarn, chab - tooltip texts
gagman - new icon

Follow development [url=https://github.com/Tapsa/AGE/commits/master]here[/url] and [url=https://github.com/Tapsa/genieutils/commits/master]here[/url].

[url=http://aok.heavengames.com/blacksmith/lister.php?&pauthor=keisari%20tapsa&start=0&s=d&o=d]My other uploads[/url].
