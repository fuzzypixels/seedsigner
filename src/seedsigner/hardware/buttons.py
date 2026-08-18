import logging
from typing import List
import RPi.GPIO as GPIO
import time

from seedsigner.models.settings import Settings, SettingsConstants
from seedsigner.models.singleton import Singleton

logger = logging.getLogger(__name__)


class HardwareButtons(Singleton):
    if GPIO.RPI_INFO['P1_REVISION'] == 3: #This indicates that we have revision 3 GPIO
        logger.info("Detected 40pin GPIO (Raspberry Pi 2 and above)")
        KEY_UP_PIN = 31
        KEY_DOWN_PIN = 35
        KEY_LEFT_PIN = 29
        KEY_RIGHT_PIN = 37
        KEY_PRESS_PIN = 33

        KEY1_PIN = 40
        KEY2_PIN = 38
        KEY3_PIN = 36

    else:
        logger.info("Assuming 26 Pin GPIO (Raspberry Pi 1)")
        KEY_UP_PIN = 5
        KEY_DOWN_PIN = 11
        KEY_LEFT_PIN = 3
        KEY_RIGHT_PIN = 15
        KEY_PRESS_PIN = 7

        KEY1_PIN = 16
        KEY2_PIN = 12
        KEY3_PIN = 8


    @classmethod
    def get_instance(cls):
        # This is the only way to access the one and only instance
        if cls._instance is None:
            cls._instance = cls.__new__(cls)

            #init GPIO
            GPIO.setmode(GPIO.BOARD)
            GPIO.setup(HardwareButtons.KEY_UP_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)    # Input with pull-up
            GPIO.setup(HardwareButtons.KEY_DOWN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Input with pull-up
            GPIO.setup(HardwareButtons.KEY_LEFT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # Input with pull-up
            GPIO.setup(HardwareButtons.KEY_RIGHT_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP) # Input with pull-up
            GPIO.setup(HardwareButtons.KEY_PRESS_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP) # Input with pull-up
            GPIO.setup(HardwareButtons.KEY1_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)      # Input with pull-up
            GPIO.setup(HardwareButtons.KEY2_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)      # Input with pull-up
            GPIO.setup(HardwareButtons.KEY3_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)      # Input with pull-up

            cls._instance.GPIO = GPIO
            cls._instance.override_ind = False

            # Track state over time so we can apply input delays/ignores as needed
            cls._instance.cur_input = None           # Track which direction or button was last pressed
            cls._instance.cur_input_started = None   # Track when that input began
            cls._instance.last_input_time = int(time.time() * 1000)  # How long has it been since the last input?
            cls._instance.first_repeat_threshold = 225  # Long-press time required before returning continuous input
            cls._instance.next_repeat_threshold = 250  # Amount of time where we no longer consider input a continuous hold

        return cls._instance


    def _dpad_rotation_offset(self) -> int:
        """
        Number of 90-degree clockwise steps the d-pad's physical pins must be shifted
        by to compensate for the current SETTING__SCREEN_ROTATION.
        """
        screen_rotation = int(Settings.get_instance().get_value(SettingsConstants.SETTING__SCREEN_ROTATION, default_if_none=True))
        return (screen_rotation // 90) % 4


    def _to_physical_key(self, logical_key: int) -> int:
        """
        Translates a logical (i.e. as perceived by the user, post-rotation) d-pad
        direction into the physical GPIO pin that must be polled to detect it.
        Non-directional keys (KEY_PRESS, KEY1-3) pass through unchanged.
        """
        dpad = HardwareButtonsConstants.DPAD_PHYSICAL_CLOCKWISE
        if logical_key not in dpad:
            return logical_key
        offset = self._dpad_rotation_offset()
        if offset == 0:
            return logical_key
        logical_index = dpad.index(logical_key)
        return dpad[(logical_index - offset) % 4]


    def _to_logical_key(self, physical_key: int) -> int:
        """ The inverse of `_to_physical_key`: physical pin -> perceived direction. """
        dpad = HardwareButtonsConstants.DPAD_PHYSICAL_CLOCKWISE
        if physical_key not in dpad:
            return physical_key
        offset = self._dpad_rotation_offset()
        if offset == 0:
            return physical_key
        physical_index = dpad.index(physical_key)
        return dpad[(physical_index + offset) % 4]


    def wait_for(self, keys=[]) -> int:
        """
        Block execution until one of the target keys is pressed.

        `keys` and the returned value are in logical (i.e. as the user currently
        perceives them, post-SETTING__SCREEN_ROTATION) terms; the actual GPIO polling
        below is translated to/from the physical pins.

        Optionally override the wait by calling `trigger_override()`.
        """
        # TODO: Refactor to keep control in the Controller and not here
        from seedsigner.controller import Controller
        controller = Controller.get_instance()
        self.override_ind = False

        keys = [self._to_physical_key(key) for key in keys]

        while True:
            if self.override_ind:
                # Break out of the wait_for without waiting for user input
                self.override_ind = False
                return HardwareButtonsConstants.OVERRIDE

            cur_time = int(time.time() * 1000)
            if cur_time - self.last_input_time > controller.screensaver_activation_ms and controller.is_screensaver_start_allowed:
                # Start the screensaver. Will block execution until input detected.
                controller.start_screensaver()

                # We're back. Update last_input_time to now.
                self.update_last_input_time()

                # Freeze any further processing for a moment to avoid having the wakeup
                #   input register in the resumed UI.
                time.sleep(self.next_repeat_threshold / 1000.0)

                # Resume from a fresh loop
                continue

            # Check each candidate key to see if it was pressed
            for key in keys:
                if self.GPIO.input(key) == GPIO.LOW:
                    if self.cur_input != key:
                        self.cur_input = key
                        self.cur_input_started = int(time.time() * 1000)  # in milliseconds
                        self.last_input_time = self.cur_input_started
                        return self._to_logical_key(key)

                    else:
                        # Still pressing the same input
                        if cur_time - self.last_input_time > self.next_repeat_threshold:
                            # Too much time has elapsed to consider this the same
                            #   continuous input. Treat as a new separate press.
                            self.cur_input_started = cur_time
                            self.last_input_time = cur_time
                            return self._to_logical_key(key)

                        elif cur_time - self.cur_input_started > self.first_repeat_threshold:
                            # We're good to relay this immediately as continuous
                            #   input.
                            self.last_input_time = cur_time
                            return self._to_logical_key(key)

                        else:
                            # We're not yet at the first repeat threshold; triggering
                            #   a key now would be too soon and yields a bad user
                            #   experience when only a single click was intended but
                            #   a second input is processed because of race condition
                            #   against human response time to release the button.
                            # So there has to be a delay before we allow the first
                            #   continuous repeat to register. So we'll ignore this
                            #   round's input and **won't update any of our
                            #   timekeeping vars**. But once we cross the threshold,
                            #   we let the repeats fly.
                            pass

            time.sleep(0.01) # wait 10 ms to give CPU chance to do other things


    def update_last_input_time(self):
        self.last_input_time = int(time.time() * 1000)


    def trigger_override(self) -> bool:
        """ Set the override flag to break out of the current `wait_for` loop """
        self.override_ind = True


    def check_for_low(self, key: int = None, keys: List[int] = None) -> bool:
        """ Returns True if one of the target keys/key is pressed. `key`/`keys` are logical. """
        if key:
            keys = [key]
        keys = [self._to_physical_key(key) for key in keys]
        for key in keys:
            if self.GPIO.input(key) == self.GPIO.LOW:
                self.update_last_input_time()
                return True
        else:
            return False


    def has_any_input(self) -> bool:
        """ Returns True if any of the keys are pressed """
        for key in HardwareButtonsConstants.ALL_KEYS:
            if self.GPIO.input(key) == GPIO.LOW:
                return True
        return False


# class used as short hand for static button/channel lookup values
class HardwareButtonsConstants:
    if GPIO.RPI_INFO['P1_REVISION'] == 3: #This indicates that we have revision 3 GPIO
        KEY_UP = 31
        KEY_DOWN = 35
        KEY_LEFT = 29
        KEY_RIGHT = 37
        KEY_PRESS = 33

        KEY1 = 40
        KEY2 = 38
        KEY3 = 36
    else:
        KEY_UP = 5
        KEY_DOWN = 11
        KEY_LEFT = 3
        KEY_RIGHT = 15
        KEY_PRESS = 7

        KEY1 = 16
        KEY2 = 12
        KEY3 = 8

    OVERRIDE = 1000

    ALL_KEYS = [
        KEY_UP,
        KEY_DOWN,
        KEY_LEFT,
        KEY_RIGHT,
        KEY_PRESS,
        KEY1,
        KEY2,
        KEY3,
    ]

    KEYS__LEFT_RIGHT_UP_DOWN = [KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN]
    KEYS__ANYCLICK = [KEY_PRESS, KEY1, KEY2, KEY3]

    # The 4-way d-pad's *physical* GPIO pins, in clockwise order starting from the
    # factory-default "up" pin. Used to remap the d-pad when the screen (and the PCB
    # it's mounted on) has been physically rotated; see
    # SettingsConstants.SETTING__SCREEN_ROTATION. KEY_PRESS/KEY1/KEY2/KEY3 are not
    # directional and are unaffected by screen rotation.
    DPAD_PHYSICAL_CLOCKWISE = [KEY_UP, KEY_RIGHT, KEY_DOWN, KEY_LEFT]
