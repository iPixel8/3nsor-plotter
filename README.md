# 3nsor - the BrickPi rope plotter #

[![Plotter in action](http://img.youtube.com/vi/YYG3XGfyVHk/0.jpg)](http://www.youtube.com/watch?v=YYG3XGfyVHk)


This plotter is hung by two pieces of rope on a door and controlled by a BrickPi. Since BrickPi has no screen I built
a web interface with tornado. The video is done with the BrickPi branch of this repo. The newest branch is on Ev3dev.

## Hardware requirements ##
- Raspberry Pi with a BrickPi or BrickPi+ shield. The model doesn't matter, model A, B, B+ and v2 all work.
- ...OR an Ev3 brain
- A robot with two ropes attached to motors B and C, and a pen to motor A.
- A small USB wifi stick. Can't leave the robot connected while it's plotting

## Software requirements ##
- **Tornado**
- **Ev3dev**

## Installation ##

1. Get yourself a micro SD with the right Ev3dev image. They exist for both BrickPi and Ev3. http://www.ev3dev.org/docs/getting-started/
2. Install [Tornado](http://www.tornadoweb.org/en/stable/#installation) like so:
``` sh
sudo apt-get install build-essential python3-dev
git clone https://github.com/tornadoweb/tornado
cd tornado
python3 setup.py build
sudo python3 setup.py install
```

2. Clone this repo: `git clone https://github.com/antonvh/3nsor-plotter/`
5. To get battery voltage readings on a BrickPi add `i2c-dev` to `/etc/modules-load.d/modules.conf` and reboot.

## Usage ##

2. Type `cd 3nsor-plotter`. This is important, as tornado expects to be run from it's root.
2. Type `python3 3nsor-plotter.py`
3. Surf to http://yourbrickaddress:9093
4. Move the plotter to the origin with the buttons/keyboard
4. Measure and input rope lengths at origin. (Make sure your paper is in the middle between attachment points.)
5. Zero it all
6. Generate a coords.csv file using the l3onardo script. https://github.com/antonvh/L3onardo-plotter
6. Upload the file
6. Start plotting

## Please fork me ##
And help improve the web interface.

## Good to know ##
- On the Raspberry Pi, My wifi dongle went to sleep all the time, while I was working on this project. I used this to fix it: http://raspberrypi.stackexchange.com/questions/1384/how-do-i-disable-suspend-mode
As long as the server is running, there's no problem.
- The script has virtually no error catching. It will crash if you throw data at it that it is not expecting.

## To do ##
- When a plotter instance exists, prefill actual parameters on webpage
- Refactor and clean up the code.
- Move web UI stuff to a separate folder
- Make routine for writing %done and status on ev3 screen
- Improve speed of uploads
- Convert print statements to logging statements see: http://stackoverflow.com/questions/15707056/get-time-of-execution-of-a-block-of-code-in-python-2-7
- Add command line parameter to set log levels
- Develop a status/voltage gauge on the web front-end