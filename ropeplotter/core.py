__author__ = 'anton'

import time
import ev3dev.auto as ev3
import math
from PIL import Image, ImageFilter
from ropeplotter.robot_helpers import PIDMotor, clamp, BrickPiPowerSupply
import logging

plotter_log = logging.getLogger("Plotter")

PEN_UP_POS = 0
PEN_DOWN_POS = -90
UP = 0
DOWN = 1
UNCHANGED = -1
SLOW = 600 # 320
FAST = 600 # 520

class RopePlotter(object):
    def __init__(self, l_rope_0, r_rope_0, attachment_distance, cm_to_deg=-175, Kp=2.2, Ki=0.2, Kd=0.02, chalk=False):

        self.cm_to_deg = cm_to_deg
        self.__l_rope_0 = float(l_rope_0)
        self.__r_rope_0 = float(r_rope_0)
        self.__att_dist = float(attachment_distance)
        self.direction = 1 # -1 is for reversing motors
        self.calc_constants()
        self.scanlines = 100
        self.r_step = 2.0 # cm

        # Start the engines
        self.pen_motor = PIDMotor(ev3.OUTPUT_A, Kp=2, Ki=0.1, Kd=0, brake=0.1, speed_reg=True)
        self.pen_motor.positionPID.precision = 10
        self.left_motor = PIDMotor(ev3.OUTPUT_B, Kp=Kp, Ki=Ki, Kd=Kd)
        self.left_motor.stop_action = 'brake'
        self.right_motor = PIDMotor(ev3.OUTPUT_C, Kp=Kp, Ki=Ki, Kd=Kd)
        self.right_motor.stop_action = 'brake'

        # Build lists for iterating over all motors
        self.drive_motors = [self.left_motor, self.right_motor]
        self.all_motors = [self.left_motor, self.right_motor, self.pen_motor]

        # Set starting point
        self.set_control_zeroes()

        # Chalk extruder startup
        self.chalk = chalk
        if chalk:
            self.chalk_motor = ev3.Motor(ev3.OUTPUT_D)
            self.chalk_sensor = ev3.TouchSensor(ev3.INPUT_4)

            # Drive slowly to find the end position
            self.chalk_motor.run_direct(duty_cycle_sp=-50)
            self.chalk_motor.wait_until('stalled')
            self.chalk_motor.stop()
            self.chalk_motor.position = -10
            self.right_motor.polarity = 'inversed'

    # Getters & setters for plotter properties.
    # After setting these, some calculations need to be done, that's why I define special functions
    # And decorate them as setters and getters.

    @property
    def Kp(self):
        return self.drive_motors[0].positionPID.Kp

    @Kp.setter
    def Kp(self,Kp):
        for motor in self.drive_motors:
            motor.positionPID.Kp = float(Kp)

    @property
    def Ti(self):
        return self.drive_motors[0].positionPID.Ti

    @Ti.setter
    def Ti(self, Ti):
        for motor in self.drive_motors:
            motor.positionPID.Ti = float(Ti)

    @property
    def Td(self):
        return self.drive_motors[0].positionPID.Td

    @Td.setter
    def Td(self, Td):
        for motor in self.drive_motors:
            motor.positionPID.Td = float(Td)

    @property
    def cm_to_deg(self):
        return self.__cm_to_deg

    @cm_to_deg.setter
    def cm_to_deg(self, setting):
        if ev3.current_platform == 'brickpi':
            self.battery = BrickPiPowerSupply()
            factor = 2
        else:
            self.battery = ev3.PowerSupply()
            factor = 1
        self.__cm_to_deg = factor * int(setting)

    @property
    def l_rope_0(self):
        return self.__l_rope_0

    @l_rope_0.setter
    def l_rope_0(self, length):
        self.__l_rope_0 = float(length)
        self.calc_constants()

    @property
    def r_rope_0(self):
        return self.__r_rope_0

    @r_rope_0.setter
    def r_rope_0(self, length):
        self.__r_rope_0 = float(length)
        self.calc_constants()

    @property
    def att_dist(self):
        return self.__att_dist

    @att_dist.setter
    def att_dist(self,length):
        self.__att_dist = float(length)
        self.calc_constants()

    @staticmethod
    def triangle_area(a, b, c):
        """
        Calculate the area of a triangle by the lengths of it's sides using Heron's formula

        :param a: Length of side a
        :param b: Length of side b
        :param c: Length of side c
        :return: area (float)
        """
        half_p = (a + b + c) / 2
        return (half_p * (half_p - a) * (half_p - b) * (half_p - c)) ** 0.5

    def calc_constants(self):
        # Calculate the height of triangle made up by the two ropes
        self.v_margin = self.triangle_area(self.__l_rope_0, self.__r_rope_0, self.__att_dist) / self.__att_dist * 2

        # Using pythagoras to find distance from bottom triangle point to left doorframe
        self.h_margin = (self.__l_rope_0 ** 2 - self.v_margin ** 2) ** 0.5

        # For convenience, the canvas is square and centered between the attachment points
        self.canvas_size = self.__att_dist - 2 * self.h_margin

    ### Calculations for global (doorframe) to local (canvas) coordinates and back. ###
    def motor_targets_from_norm_coords(self,x_norm, y_norm):
        x,y = self.normalized_to_global_coords(x_norm,y_norm)
        return self.motor_targets_from_coords(x,y)

    def motor_targets_from_coords(self,x, y):
        l_rope = (x ** 2 + y ** 2) ** 0.5
        r_rope = ((self.__att_dist - x) ** 2 + y ** 2) ** 0.5
        l_target = (l_rope - self.__l_rope_0) * self.cm_to_deg
        r_target = (r_rope - self.__r_rope_0) * self.cm_to_deg

        return int(l_target), int(r_target)

    def coords_from_motor_pos(self,l_motor,r_motor):
        l_rope = l_motor / self.cm_to_deg + self.__l_rope_0
        r_rope = r_motor / self.cm_to_deg + self.__r_rope_0
        y = self.triangle_area(l_rope,r_rope,self.att_dist)*2/self.att_dist
        x = (l_rope**2 - y**2)**0.5
        y_norm = (y - self.v_margin)/self.canvas_size
        x_norm = (x - self.h_margin)/self.canvas_size

        return x_norm,y_norm

    def normalized_to_global_coords(self, x_norm, y_norm):
        # convert normalized coordinates to global coordinates
        x = x_norm * self.canvas_size + self.h_margin
        y = y_norm * self.canvas_size + self.v_margin

        return x, y

    ### Movement functions ###

    def set_control_zeroes(self):
        for motor in self.drive_motors:
            motor.position = 0
            #motor.positionPID.zero = motor.position
        self.pen_motor.position = PEN_UP_POS

    def move_to_coord(self,x,y, brake=False, pen=-1):
        motor_b_target, motor_c_target  = self.motor_targets_from_coords(x, y)
        self.move_to_targets((motor_b_target, motor_c_target), brake, pen)

    def move_to_norm_coord(self, x_norm, y_norm, pen=UNCHANGED, brake=False):
        motor_b_target, motor_c_target = self.motor_targets_from_norm_coords(x_norm, y_norm)
        self.move_to_targets((motor_b_target, motor_c_target),pen=pen, brake=brake)

    def move_to_targets(self, targets, brake=False, pen=-1):

        # Set targets
        for motor, tgt in zip(self.drive_motors, targets):
            motor.position_sp = tgt

        if pen == 1:        # Put the pen down
            self.pen_down()
        elif pen == 0:      # Put the pen up
            self.pen_up()


        # Now run the motors and wait for the motors to reach their targets
        # Alas ev3dev's run_to_abs_pos is not usable on BrickPi. So I emulate a PID controller.

        while 1:
            for motor in self.drive_motors:
                motor.run()

            if self.chalk and self.pen_motor.position_sp == PEN_DOWN_POS:
                # Extrude chalk if needed.

                if self.chalk_sensor.is_pressed:
                    self.chalk_motor.run_direct(duty_cycle_sp=60)
                    if self.chalk_motor.position > 20552:
                        self.reload_chalk()
                else:
                    self.chalk_motor.stop()

            if all([motor.positionPID.target_reached for motor in self.drive_motors]):
                if brake: #Run a little while long to stay in position.
                    t=time.time()+0.7
                    while t > time.time():
                        for motor in self.drive_motors:
                            motor.run()
                self.left_motor.stop()
                self.right_motor.stop()
                self.pen_motor.stop()
                break

            #We're done calculating and setting all motor speeds!
            time.sleep(0.016)

    def reload_chalk(self):
        if self.chalk:
            # Drive the loader back and wait for human to insert new chalk and resume
            self.chalk_motor.run_to_abs_pos(position_sp=0, speed_sp=600)
            self.chalk_motor.wait_while('running')
            buttons = ev3.Button()
            while not buttons.enter():
                time.sleep(0.5)

    ### Advanced plotting functions by chaining movement functions ###

    def test_drive(self):
        # A little disturbance in the force
        self.move_to_norm_coord(0.0,0.5)
        self.move_to_norm_coord(0.3,0.3)
        self.move_to_norm_coord(0.0,0.0)

    def plot_from_file(self, filename):
        """
        Generator function for plotting from coords.csv file. After each next() it returns the pct done of the plotting
        This way the plotting can easily be aborted and status can be given. Gotta love python for this.
        Usage:

        gen = plotter.plot_from_file(myfile)
        while 1:
            try:
                pct_done = next(gen)
            except StopIteration:
                break

        :param filename: str
        :return: percentage done: float
        """
        coords = open(filename)
        num_coords = int(coords.readline())  #coords contains it's length on the first line.

        #drive to the first coordinate
        self.pen_up()
        # read from file
        x_norm, y_norm = [float(n) for n in coords.readline().split(",")]
        #move
        self.move_to_norm_coord(x_norm, y_norm)
        self.pen_down()
        for i in range(num_coords - 1):
            # read from file
            x_norm, y_norm = [float(n) for n in coords.readline().split(",")]
            #move
            self.move_to_norm_coord(x_norm, y_norm)
            yield float(i+1)/num_coords*100

        coords.close()
        self.pen_up()
        self.move_to_norm_coord(0, 0)
        yield 100

    def plot_circle_waves(self):
        """
        Draws a grayscale image of the uploaded photo by tracing the canvas with circles and
        oscilating more in darker areas.

        This is a generator method that yields progress and can be paused after each progress report.

        :param num_circles: The amount of circles to put on the complete canvas.
        :yield: progress.
        """

        # Load grayscale image
        im = Image.open("uploads/picture.jpg").convert("L")
        w, h = im.size
        pixels = im.load()

        # Calculate circles, smallest, largest and offset.
        r_min = (self.h_margin ** 2 + self.v_margin ** 2) ** 0.5
        r_max = ((self.h_margin + self.canvas_size) ** 2 + (self.v_margin + self.canvas_size) ** 2) ** 0.5
        r_step = (r_max - r_min) / self.scanlines
        amplitude = r_step * self.cm_to_deg / 2 * 1.15  # Sine amplitude in motor degrees
        half_wavelength = 0.5                           # Time in seconds it takes to draw half a sine wave.

        anchor_motor, drive_motor = self.drive_motors

        # Draw circles with left anchor point as center.
        for i in range(1, self.scanlines, 2):
            # Find the starting point x,y
            # where a circle with radius r_min+r_step*i crosses the left margin.
            x = self.h_margin
            y = ((r_min + r_step * i) ** 2 - self.h_margin ** 2) ** 0.5

            # Check whether we reached the bottom
            if y > self.v_margin + self.canvas_size:
                # Now we check where circles cross the bottom margin
                x = ((r_min + r_step * i) ** 2 - (self.v_margin + self.canvas_size) ** 2) ** 0.5
                y = self.v_margin + self.canvas_size

            self.move_to_coord(x, y, brake=True, pen=0)

            #Intialise
            anchor_line = anchor_motor.position
            next_sample_time = time.time()
            darkness = 0
            weighted_amplitude = 0

            # Start driving (up)
            drive_motor.run_forever(speed_sp=100)
            while 1:
                # In each loop read motor positions.
                drive_motor_pos = drive_motor.position
                anchor_motor_pos = anchor_motor.position

                now = time.time()

                x_norm, y_norm = self.coords_from_motor_pos(anchor_motor_pos, drive_motor_pos)
                # Look at the pixel we're at and move pen up & down according to it's darkness
                pixel_location = (clamp(x_norm * w, (0, w - 1)), clamp(y_norm * w, (0, h - 1)))
                darkness = (pixels[pixel_location] - 255.0) / -255.0
                drive_speed = 600 - 578 * darkness ** 0.9   # Exponential darkness for more contrast.

                if darkness > 0.2:
                    self.pen_motor.position_sp = PEN_DOWN_POS
                    if not self.pen_motor.positionPID.target_reached:
                        drive_motor.stop()
                    else:
                        drive_motor.run_forever(speed_sp=drive_speed)
                else:
                    self.pen_motor.position_sp = PEN_UP_POS

                if now >= next_sample_time:
                    weighted_amplitude = amplitude * darkness # this turns 0 when white (255), 1 when black.
                    next_sample_time = now + half_wavelength

                drive_motor.run_forever(speed_sp=drive_speed) # Exponential darkness for more contrast.
                anchor_motor.position_sp = anchor_line + math.sin(now * math.pi / half_wavelength) * weighted_amplitude
                anchor_motor.run()
                self.pen_motor.run()

                if y_norm <= 0:
                    break  # reached the top
                if x_norm >= 1:
                    break  # reached the right side

            anchor_motor.stop()
            drive_motor.stop()

            # Yield to allow pause/stop and show percentage completion
            yield (i * 50.0) / self.scanlines

            # Good, now move to the next point and roll down.
            x = ((r_min + r_step * (i + 1)) ** 2 - self.v_margin ** 2) ** 0.5
            y = self.v_margin

            if x > (self.h_margin + self.canvas_size):  # Reached right side
                x = self.h_margin + self.canvas_size
                y = ((r_min + r_step * (i + 1)) ** 2 - (self.h_margin + self.canvas_size) ** 2) ** 0.5

            self.move_to_coord(x, y, brake=True, pen=0)

            # Start driving down
            anchor_line = anchor_motor.position
            drive_motor.run_forever(speed_sp=-100)
            while 1:
                drive_motor_pos = drive_motor.position
                anchor_motor_pos = anchor_motor.position

                now = time.time()

                #Get our current location in normalised coordinates.
                x_norm, y_norm = self.coords_from_motor_pos(anchor_motor_pos, drive_motor_pos)
                pixel_location = (clamp(x_norm * w, (0, w - 1)), clamp(y_norm * w, (0, h - 1)))
                darkness = (pixels[pixel_location] - 255.0) / -255.0  # this turns 0 when white (255), 1 when black.
                drive_speed = (600 - 578 * darkness ** 0.9) * -1  # Exponential darkness for more contrast.

                if darkness > 0.2:
                    self.pen_motor.position_sp = PEN_DOWN_POS
                    if not self.pen_motor.positionPID.target_reached:
                        drive_motor.stop()
                    else:
                        drive_motor.run_forever(speed_sp=drive_speed)
                else:
                    self.pen_motor.position_sp = PEN_UP_POS

                if now >= next_sample_time:
                    weighted_amplitude = amplitude * darkness
                    next_sample_time = now + half_wavelength

                drive_motor.run_forever(speed_sp=drive_speed)
                anchor_motor.position_sp = anchor_line + math.sin(now * math.pi / half_wavelength) * weighted_amplitude
                anchor_motor.run()
                self.pen_motor.run()

                if y_norm >= 1:
                    break  # reached the bottom
                if x_norm <= 0:
                    break  # reached the left side

            anchor_motor.stop()
            drive_motor.stop()

            # Yield to allow pause/stop and show percentage
            yield ((i + 1) * 50.0) / self.scanlines

        self.pen_up()
        self.move_to_norm_coord(0,0)

    def plot_circles(self):
        num_circles = self.scanlines

        im = Image.open("uploads/picture.jpg").convert("L")
        w, h = im.size
        pixels = im.load()

        r_min = (self.h_margin**2+self.v_margin**2)**0.5
        r_max = ((self.h_margin+self.canvas_size)**2 + (self.v_margin+self.canvas_size)**2)**0.5
        r_step = (r_max-r_min)/num_circles

        for right_side_mode in [0, 1]: # Multiply all terms that only apply to the right side circles with right_side_mode
            if right_side_mode:
                drive_motor, anchor_motor = self.drive_motors
            else:
                anchor_motor, drive_motor = self.drive_motors

            # First draw circles with left anchor point as center.
            for i in range(1, num_circles, 2):
                # Move to the starting point at x,y
                # Calculate where a circle with radius r_min+r_step*i crosses the left margin.
                x = self.h_margin + (right_side_mode * self.canvas_size)
                y = ((r_min+r_step*i)**2 - self.h_margin ** 2) ** 0.5   # This is the same left and right
                if y > self.v_margin+self.canvas_size:
                    # We reached the bottom, now we check where circles cross the bottom margin
                    if right_side_mode:
                        x = self.h_margin*2 + self.canvas_size - ((r_min + r_step*i) ** 2 - (self.v_margin + self.canvas_size) ** 2) ** 0.5
                    else:
                        x = ((r_min + r_step*i) ** 2 - (self.v_margin + self.canvas_size) ** 2) ** 0.5
                    y = self.v_margin+self.canvas_size  # This is the same left and right
                self.move_to_coord(x, y, pen=UP, brake=True)
                # Yield to allow pause/stop and show percentage
                yield (i * 50.0 + right_side_mode * 50.0) / num_circles * 0.66

                # Now calculate coordinates continuously until we reach the top, or right side of the canvas
                # Motor B is off, so let's get it's encoder only once
                while 1:
                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    pixel_location = (clamp(x_norm * w, (0,w-1)), clamp(y_norm * w, (0,h-1)))
                    if pixels[pixel_location] < 120 + 60 * right_side_mode:
                        self.pen_motor.position_sp = PEN_DOWN_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=FAST)
                    self.pen_motor.run()

                    if y_norm <= 0:
                        break # reached the top
                    if (not right_side_mode) and x_norm >= 1:
                        break # reached the right side
                    if right_side_mode and x_norm <= 0:
                        break

                drive_motor.stop()


                #Good, now move to the next point and roll down.
                if right_side_mode:
                    x = self.h_margin*2 + self.canvas_size - ((r_min + r_step*(i+1)) ** 2 - self.v_margin ** 2) ** 0.5
                else:
                    x = ((r_min + r_step*(i+1)) ** 2 - self.v_margin ** 2) ** 0.5
                y = self.v_margin

                if (not right_side_mode) and x > (self.h_margin + self.canvas_size): # Reached right side
                    x = self.h_margin + self.canvas_size
                    y = ((r_min+r_step*(i+1)) ** 2 - (self.h_margin+self.canvas_size) ** 2) ** 0.5

                if right_side_mode and x < self.h_margin: # Reached left side
                    x = self.h_margin
                    y = ((r_min+r_step*(i+1)) ** 2 - (self.h_margin+self.canvas_size) ** 2) ** 0.5

                self.move_to_coord(x, y, pen=UP, brake=True)
                # Yield to allow pause/stop and show percentage
                yield ((i+1)*50.0+right_side_mode*50.0)/num_circles * 0.66

                # Calculate coordinates continuously until we reach the top, or right side of the canvas
                while 1:
                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    pixel_location = (int(clamp(x_norm * w, (0,w-1))), int(clamp(y_norm * w, (0,h-1))))

                    if pixels[pixel_location] < 120 + 60 * right_side_mode:
                        self.pen_motor.position_sp = PEN_DOWN_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=-SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=-FAST)
                    self.pen_motor.run()

                    if y_norm >= 1:
                        break # reached the bottom
                    if x_norm <= 0 and not right_side_mode:
                        break # reached the left side
                    if x_norm >= 1 and right_side_mode:
                        break
                    time.sleep(0.02)

                drive_motor.stop()

        # Now draw horizontalish lines.
        self.pen_up()
        for i in range(0,num_circles, 2):
            x = self.h_margin
            y = self.v_margin + i * self.canvas_size * 1.0 / num_circles
            self.move_to_coord(x, y, pen=UP, brake=True)
            yield 66 + i * 33.33 / num_circles

            while 1:
                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    pixel_location = (clamp(x_norm * w, (0,w-1)), clamp(y_norm * w, (0,h-1)))

                    if pixels[pixel_location] < 60:
                        self.pen_motor.position_sp = PEN_DOWN_POS
                        if not self.pen_motor.positionPID.target_reached:
                            self.right_motor.stop()
                            self.left_motor.stop()
                        else:
                            self.right_motor.run_forever(speed_sp=SLOW)
                            self.left_motor.run_forever(speed_sp=-SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            self.right_motor.stop()
                            self.left_motor.stop()
                        else:
                            self.right_motor.run_forever(speed_sp=FAST)
                            self.left_motor.run_forever(speed_sp=-FAST)
                    self.pen_motor.run()

                    if x_norm >= 1:
                        break

            self.right_motor.stop()
            self.left_motor.stop()

            x = self.h_margin + self.canvas_size
            y = self.v_margin + (i+1) * self.canvas_size * 1.0 / num_circles
            self.move_to_coord(x, y, pen=UP, brake=True)
            yield 66 + (i+1) * 33.33 / num_circles

            while 1:

                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    pixel_location = (clamp(x_norm * w, (0,w-1)), clamp(y_norm * w, (0,h-1)))
                    if pixels[pixel_location] < 60:
                        self.pen_motor.position_sp = PEN_DOWN_POS
                        if not self.pen_motor.positionPID.target_reached:
                            self.right_motor.stop()
                            self.left_motor.stop()
                        else:
                            self.right_motor.run_forever(speed_sp=-SLOW)
                            self.left_motor.run_forever(speed_sp=SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            self.right_motor.stop()
                            self.left_motor.stop()
                        else:
                            self.right_motor.run_forever(speed_sp=-FAST)
                            self.left_motor.run_forever(speed_sp=FAST)
                    self.pen_motor.run()

                    if x_norm <= 0:
                        break

            self.right_motor.stop()
            self.left_motor.stop()

        self.move_to_norm_coord(0,0,pen=UP, brake=True)

    def optimized_etch(self):
        # load image
        im = Image.open("uploads/picture.jpg").convert("L")
        levels = [180, 120, 65]
        for i in range(3):
            # make all pixels with brightness between 0 and levels[i] white, the rest black.
            etch_area = Image.eval(im, lambda x: (x < levels[i]) * 255)
            yield "Pixels < " + str(levels[i]) + " selected"
            # Get the Bounding rectangle of the result
            bbox = etch_area.getbbox()
            yield "Plotting inside " + str(bbox)
            # create a blurred version to slow the robot down when it nears a white area
            # im_blur = etch_area.filter(ImageFilter.GaussianBlur(20))
            # yield "Image blurred"
            # composite the images to create map for robot speed and pen up/down
            # etch_area = Image.composite(etch_area, im_blur, etch_area)
            # yield "Image composited"
            # etch only the resulting rectangle with the resulting image
            plot_action = self.etch_region(bbox, etch_area, i)
            while True:
                try:
                    message = "{0:.2f}% done".format(next(plot_action))
                    plotter_log.info(message)
                    yield message
                except StopIteration:
                    break

        self.move_to_norm_coord(0, 0, pen=UP, brake=True)

    def etch_region(self, bbox, im, direction):
        w, h = im.size
        pixels = im.load()

        # convert bbox to absolute global coordinates in cm
        # The bounding box is returned as a 4-tuple defining the left, upper, right, and lower pixel
        left, top = self.normalized_to_global_coords(float(bbox[0]) / w, float(bbox[1]) / w)
        right, bottom = self.normalized_to_global_coords(float(bbox[2]) / w, float(bbox[3]) / w)

        r_step = self.r_step

        if direction < 2:
            # Calculate the number of circles to be drawn.
            r_min = (left ** 2 + top ** 2) ** 0.5
            r_max = (right ** 2 + bottom ** 2) ** 0.5
            num_circles = round((r_max - r_min) / r_step)

            right_side_mode = direction
            if right_side_mode:
                drive_motor, anchor_motor = self.drive_motors
            else:
                anchor_motor, drive_motor = self.drive_motors

            for i in range(1, num_circles, 2):
                # Move to the starting point at x,y which is slightly below the top left of the rectangle
                # Calculate where a circle with radius r_min+r_step*i crosses the left (or right) margin.
                if right_side_mode:
                    x = right
                    y = ((r_min+r_step*i)**2 - (self.att_dist - x) ** 2) ** 0.5
                else:
                    x = left
                    y = ((r_min + r_step * i) ** 2 - x ** 2) ** 0.5
                if y >= bottom:
                    # We reached the bottom, now we check where circles cross the bottom margin
                    if right_side_mode:
                        x = self.att_dist - ((r_min + r_step*i) ** 2 - bottom ** 2) ** 0.5
                    else:
                        x = ((r_min + r_step*i) ** 2 - bottom ** 2) ** 0.5
                    y = bottom  # This is the same left and right

                self.move_to_coord(x, y, pen=UP, brake=True)
                # Yield to allow pause/stop and show percentage
                yield (i * 50.0 + right_side_mode * 50.0) / num_circles * 0.66

                # Now calculate coordinates continuously until we reach the top, or right side of the canvas
                # Motor B is off, so let's get it's encoder only once
                while 1:
                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    x, y = self.normalized_to_global_coords(x_norm, y_norm)
                    pixel_location = (clamp(x_norm * w, (0, w-1)), clamp(y_norm * w, (0, h-1)))
                    if pixels[pixel_location] == 255:
                        self.pen_motor.position_sp = PEN_DOWN_POS

                        while self.chalk_sensor.is_pressed:
                            drive_motor.stop()
                            self.chalk_motor.run_forever(speed_sp=300)
                        self.chalk_motor.stop()

                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            # Calculate speed. At pixel value 255 it should be slow, at 0 fast, and otherwise
                            # proportionally in between.
                            speed = FAST - pixels[pixel_location] * (FAST-SLOW) // 255
                            drive_motor.run_forever(speed_sp=speed)
                    self.pen_motor.run()

                    if y <= top:    # reached the top
                        break
                    if (not right_side_mode) and x >= right:    # reached the right side
                        break
                    if right_side_mode and x <= left:           # reached the left side
                        break

                drive_motor.stop()
                self.pen_up()


                #Good, now move to the next point and roll down.
                if right_side_mode:
                    x = self.att_dist - ((r_min + r_step*(i+1)) ** 2 - top ** 2) ** 0.5
                else:
                    x = ((r_min + r_step*(i+1)) ** 2 - top ** 2) ** 0.5
                y = top

                if (not right_side_mode) and x >= right: # Reached right side
                    x = right
                    y = ((r_min+r_step*(i+1)) ** 2 - right ** 2) ** 0.5

                if right_side_mode and x <= left: # Reached left side
                    x = left
                    y = ((r_min+r_step*(i+1)) ** 2 - (self.att_dist - left) ** 2) ** 0.5

                self.move_to_coord(x, y, pen=UP, brake=True)
                # Yield to allow pause/stop and show percentage
                yield ((i+1)*33.3+right_side_mode*33.3)/num_circles

                # Calculate coordinates continuously until we reach the top, or right side of the canvas
                while 1:
                    # Look at the pixel we're at and move pen up or down accordingly
                    x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                    x, y = self.normalized_to_global_coords(x_norm, y_norm)
                    pixel_location = (int(clamp(x_norm * w, (0,w-1))), int(clamp(y_norm * w, (0,h-1))))
                    if pixels[pixel_location] == 255:
                        self.pen_motor.position_sp = PEN_DOWN_POS

                        while self.chalk_sensor.is_pressed: #pause to load chalk
                            drive_motor.stop()
                            self.chalk_motor.run_forever(speed_sp=300)
                        self.chalk_motor.stop()

                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            drive_motor.run_forever(speed_sp=-SLOW)
                    else:
                        self.pen_motor.position_sp = PEN_UP_POS
                        if not self.pen_motor.positionPID.target_reached:
                            drive_motor.stop()
                        else:
                            speed = FAST - pixels[pixel_location] * (FAST - SLOW) // 255
                            drive_motor.run_forever(speed_sp=-speed)
                    self.pen_motor.run()

                    if y >= bottom:
                        break # reached the bottom
                    if x <= left and not right_side_mode:
                        break # reached the left side
                    if x >= right and right_side_mode:
                        break
                    time.sleep(0.02)

                drive_motor.stop()
                self.pen_up()

        if direction == 2:
            # Now draw horizontalish lines.

            num_circles = round((bottom - top) / r_step)

            self.pen_up()

            for i in range(0, num_circles, 2):
                x = left
                y = top + i * r_step
                self.move_to_coord(x, y, pen=UP, brake=True)
                yield 66 + i * 33.33 / num_circles

                while 1:
                        # Look at the pixel we're at and move pen up or down accordingly
                        x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                        x, y = self.normalized_to_global_coords(x_norm, y_norm)
                        pixel_location = (clamp(x_norm * w, (0, w-1)), clamp(y_norm * w, (0, h-1)))
                        if pixels[pixel_location] == 255:

                            self.pen_motor.position_sp = PEN_DOWN_POS

                            while self.chalk_sensor.is_pressed:
                                # Pause to load chalk
                                self.right_motor.stop()
                                self.left_motor.stop()
                                self.chalk_motor.run_forever(speed_sp=300)
                            self.chalk_motor.stop()
                            # Resume

                            if not self.pen_motor.positionPID.target_reached:
                                self.right_motor.stop()
                                self.left_motor.stop()
                            else:
                                self.right_motor.run_forever(speed_sp=SLOW-100)
                                l_tgt, r_tgt = self.motor_targets_from_coords(x, top + i * r_step)
                                self.left_motor.position_sp = l_tgt
                                self.left_motor.run()
                                #self.left_motor.run_forever(speed_sp=-SLOW)
                        else:
                            self.pen_motor.position_sp = PEN_UP_POS
                            if not self.pen_motor.positionPID.target_reached:
                                self.right_motor.stop()
                                self.left_motor.stop()
                            else:
                                speed = FAST - pixels[pixel_location] * (FAST - SLOW) // 255
                                self.right_motor.run_forever(speed_sp=speed-150)
                                l_tgt, r_tgt = self.motor_targets_from_coords(x, top + i * r_step)
                                self.left_motor.position_sp = l_tgt
                                self.left_motor.run()
                                # self.left_motor.run_forever(speed_sp=-speed)
                        self.pen_motor.run()

                        if x >= right:
                            break

                self.right_motor.stop()
                self.left_motor.stop()
                self.pen_up()

                x = right
                y += r_step
                self.move_to_coord(x, y, pen=UP, brake=True)
                yield 66 + (i+1) * 33.33 / num_circles

                while 1:

                        # Look at the pixel we're at and move pen up or down accordingly
                        x_norm, y_norm = self.coords_from_motor_pos(self.drive_motors[0].position, self.drive_motors[1].position)
                        x, y = self.normalized_to_global_coords(x_norm, y_norm)
                        pixel_location = (clamp(x_norm * w, (0,w-1)), clamp(y_norm * w, (0,h-1)))
                        if pixels[pixel_location] == 255:
                            self.pen_motor.position_sp = PEN_DOWN_POS
                            if not self.pen_motor.positionPID.target_reached:
                                self.right_motor.stop()
                                self.left_motor.stop()
                            else:
                                # self.right_motor.run_forever(speed_sp=-SLOW)

                                self.left_motor.run_forever(speed_sp=SLOW-100)
                                l_tgt, r_tgt = self.motor_targets_from_coords(x, top + (i+1) * r_step)
                                self.right_motor.position_sp = r_tgt
                                self.right_motor.run()
                        else:
                            self.pen_motor.position_sp = PEN_UP_POS
                            if not self.pen_motor.positionPID.target_reached:
                                self.right_motor.stop()
                                self.left_motor.stop()
                            else:
                                speed = FAST - pixels[pixel_location] * (FAST - SLOW) // 255
                                #self.right_motor.run_forever(speed_sp=-speed)
                                self.left_motor.run_forever(speed_sp=speed-150)
                                l_tgt, r_tgt = self.motor_targets_from_coords(x, top + (i + 1) * r_step)
                                self.right_motor.position_sp = r_tgt
                                self.right_motor.run()
                        self.pen_motor.run()

                        if x <= left:
                            break

            self.right_motor.stop()
            self.left_motor.stop()
            self.pen_up()

    ### Calibration & manual movement functions ###

    def pen_up(self):
        self.pen_motor.run_to_abs_pos(position_sp=PEN_UP_POS)
        self.pen_motor.wait_while('running')

    def pen_down(self):
        self.pen_motor.run_to_abs_pos(position_sp=PEN_DOWN_POS)
        self.pen_motor.wait_while('running')
        time.sleep(0.5) # Wait a bit to avoid touch sensor bounce.
        while self.chalk_sensor.is_pressed:
            self.chalk_motor.run_forever(speed_sp=150)
            if self.chalk_motor.position > 20552:
                self.reload_chalk()

        self.chalk_motor.stop()

    def left_fwd(self):
        self.left_motor.run_direct(duty_cycle_sp=100)

    def left_stop(self):
        self.left_motor.stop()

    def left_back(self):
        self.left_motor.run_direct(duty_cycle_sp=-100)

    def right_fwd(self):
        self.right_motor.run_direct(duty_cycle_sp=100)

    def right_stop(self):
        self.right_motor.stop()

    def right_back(self):
        self.right_motor.run_direct(duty_cycle_sp=-100)

    def stop_all_motors(self):
        for motor in self.all_motors:
            motor.stop()
        print("Motors stopped")

