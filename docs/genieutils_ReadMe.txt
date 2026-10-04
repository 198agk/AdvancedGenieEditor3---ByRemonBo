genieutils

This software is licensed using GNU Lesser General Public License Version 3 (LGPLv3). This means that you are free to modify, (re)use, and (re)distribute the software as long as the license and copyright notices are preserved, and the full source code of your edition is made available. A copy of the LGPLv3 license text can be found in the "genieutils_License.txt" file in your distribution. The copyright notice for the software can be found in the source code.

For your convenience, a binary copy of the software is included with your distribution.

In accordance with the terms of GPLv3, the source code of this software is made available to you at the following location(s):
- "Source.7z\genieutils" in your distribution

The latest version of this software is available at the following URL(s):
- https://github.com/Tapsa/genieutils

Below is the ReadMe and Credits for this software.

-------------------------------------------------------------

# genieutils #

Genieutils consists of a library and some tools to work with certain data and 
resource files of genie engine games.

Notice that this library is in developement, that means the API will change.
Please also backup your files before editing to avoid file corruption
because of possible bugs.

## Features ###

 *   reading/writing of empires*.dat and genie*.dat files
 *   reading of drs, slp and pal files (partly)
 *   reading of scn/scx files (partly)
 *   reading/writing of lagnuage*.dll files (very early implementation!)
 
## Dependencies ##
 
To compile this project you will need at least:
 *   boost-iostreams >= 1.42
 *   boost-program-options >= 1.42
 *   zlib
   
If cmake finds libiconv it will automatically enable language*.dll file
support. Don't forget to init and update the submodules.

-------------------------------------------------------------

Thanks to:

dat
---

People involved in the Advanced Genie Editor from aok heavengames forum
for researching the genie engine data files.:

 * StSB77
 * Keisari Tapsa - Interface developer
 * Ancient Warrior/Taichi San - help with some data structures/beta testing
 * DiGiT - his wiki helped a lot in some places
 * qaz123tfg - writing the help files/beta tester
 * Ykkrosh - Genied1 was a big help
 * Grenadier (RvA) - terrain patch
 * DarkRain654 - for discovering unknown data
 * Sarthos
 * Sarn
 * Everyone that was involved in the geniewiki project!
 * and everyone else that was involved...

slp
---

Bryce Schroeders creator of SLPLib

smx
---

Tevious creator of SLX Studio

scn
---

DiGiT and Iberico for documenting the scn/scx format.
