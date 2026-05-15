# Terminal Titles

You know that dramatic beepity-boopity terminal text you'd get in movies from the 80s and early 90s? 
Did you ever wish you could generate that for silly meme purposes?  Well now you can!  This python 
script will take your images and a text script with markup commands and generate a video that looks 
straight out of the title sequence for a 1987 sci-fi thriller.  

https://github.com/user-attachments/assets/a0f27d8a-fb99-43da-91cd-31b6e165bdd2

-----


### Requirements

Python (obvs) with the following libraries installed:
* pillow
* moviepy
* numpy

MoviePy requires ffmpeg. Recent MoviePy installs *usually* handle this.

Fonts should be placed in the creatively-named subfolder "fonts".  Two permissively licenesed and genre-appropriate fonts are included in this repo.  The fonts retain their original license (included) and do not inheret the do-whatever-the-fuck-you-want license for the rest of this mess.

-----

### Usage

    python termtitle.py <script.txt> <output.mp4> [options]

-----

### Supported Script Commands

    [bg <file or color> <transition> <duration>] 
Set or change the background. An image file or solid color must be specified.  A transition type and duration in seconds may optionally be specified.Example: [bg image.jpg raster 2.5]   

    [clear]
Clear all visible text.   

    [pause <seconds>]
Pause with the current screen visible.  Example: [pause 1.5]

    [speed <chars-per-second>]
Change typing speed.  Example: [speed 20]

    [font <file>]
Change font. Paths are resolved relative to the script file first.  Example: [font fonts/VT323-Regular.ttf]

    [fontsize <number>]
Change font size.  Example: [fontsize 40]

    [beepfreq <hz>] 
Change beep pitch.  Example: [beepfreq 700]

    [cursor <style> <color>]
Change cursor style and optionally text/cursor color.  Styles: _, underline, |, bar, block, none.  Example: [cursor | amber]

    [color <name or hex>]
Change text/cursor color without changing cursor style.  Example: [color cyan]

    [scroll down]
Text begins near the top and advances downward. This is the default.

    [scroll up]
Text is bottom-anchored and scrolls upward as new lines are added.

    [shutdown <seconds>]
Clear text, hide cursor, collapse the screen to a horizontal white line, then fade to black. Duration is optional.  Example: [shutdown 1.8]

**Supported color names:** green, brightgreen, white, amber, orange, red, blue, cyan, magenta, purple, yellow, black   

**Supported transition styles:** cut, pixelate, raster, scroll

**See demo.txt for examples in action.  This script generated the demo video above.**

-----

### Command Line Args

    --background <file>             Optional starting background image. If omitted, starts black.
    --size <WIDTHxHEIGHT>           Output size. Default: 640x480.
    --fps <number>                  Frames per second. Default: 30.
    --font <file>                   Starting/default font file.
    --font-size <number>            Starting/default font size. Default: 26.
    --color <name or hex>           Starting/default text color. Default: green.
    --cursor <style>                Starting/default cursor style: block, underline, bar, none.
    --chars-per-second <number>     Starting/default typing speed. Default: 14.
    --end-hold <seconds>            Hold time after the script ends. Default: 2.
    --beep-frequency <hz>           Starting/default beep pitch. Default: 880.
    --beep-volume <0-1>             Beep volume. Default: 0.12.
    --beep-duration <seconds>       Beep length. Default: 0.035.
    --bg-transition <type>          Default background transition: cut, pixelize, raster, scroll.
    --bg-transition-duration <sec>  Default background transition duration. Default: 1.
    --raster-line-height <pixels>   Raster block size. Default: 8.
    --scroll-step <pixels>          Scroll transition step size. Default: 16.
    --shutdown-duration <seconds>   Default CRT shutdown duration. Default: 1.2.
    --no-audio                      Disable generated beeps.
    --no-flicker                    Disable subtle CRT brightness flicker.
    --preview-frames                Save first/middle/last preview frames as PNGs.

-----

### Disclaimer

This script is pure vibeslop.  I don't fully understand how it works.  For all I know, it could send your 
personal info directly to scammers or make your genitals explode.  Use it at your own risk.
