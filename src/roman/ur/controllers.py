'''
Module containing various arm controllers.
Controllers transform higher-level commands into lower-level commands that can be executed by the arm or by lower level controllers.
Controllers can be chained together e.g. linear motion -> touch -> ur5
Each controller is initialized with the next controller in its chain, and is required to call it.
There can be multiple active controller chains, but only one can be used at a time.
'''

import numpy as np
import math
from .arm import *
from .scripts.constants import *

class ArmController(object):
    '''
    This is the lowest level controller, communicating directly with the arm.
    There should be only one instance (per connection/arm), and all controller chains targeting the same arm must include this instance.
    '''
    def __init__(self, connection):
        self.connection = connection
        self.state = State()

    def __call__(self, cmd):
        if cmd.kind() > UR_CMD_KIND_CONFIG:
            raise Exception(f"Invalid command: {cmd.id()}")
        self.connection.send(cmd, self.state)
        if cmd.is_move_command():
            at_goal = cmd.goal_reached(self.state)
            self.state.set_state_flag(State._STATUS_FLAG_GOAL_REACHED, at_goal)
            self.state.set_state_flag(State._STATUS_FLAG_DONE, at_goal)
        return self.state

class EMAForceCalibrator(object):
    '''
    Keeps an exponentially weighted moving average of the force reported by the FT sensor.
    Adds the average to the expected force of each move command.
    Substracts the average from the reported sensor force of each response.  
    avg(t) = alpha*sample(t) + (1-alpha)*avg(t-1) 
    '''
    def __init__(self, next, alpha = 0.01):
        self.next = next
        self.alpha = alpha
        self.sample = Joints()
        self.force_average = Joints() # we are assuming the FT sensor is reset to zero on startup
        self.cmd = Command.read()
        self.state = State()

    def __call__(self, cmd):
        if cmd.is_move_command():
            self.cmd[:] = cmd
            np.add(self.force_average, cmd.force_low_bound(), self.cmd.force_low_bound().array)
            np.add(self.force_average, cmd.force_high_bound(), self.cmd.force_high_bound().array)
        
        self.state[:] = self.next(self.cmd)   
        if not self.state.is_contact():
            self.sample[:] = self.state.sensor_force()
            np.multiply(self.force_average, 1-self.alpha, self.force_average.array)
            np.multiply(self.sample, self.alpha, self.sample.array)
            np.add(self.force_average, self.sample, self.force_average.array)
        else:
            print(f"Contact detected:{self.state.sensor_force()}, outside of bounds {self.cmd.force_low_bound()} and {self.cmd.force_high_bound()}")

        np.subtract(self.state.sensor_force(), self.force_average, self.state.sensor_force().array)
        return self.state

class TouchController(object):
    '''
    Expects contact before completing the motion. Verifies that the contact is not spurrious before assuming the goal is reached.
    '''
    def __init__(self, next, validation_count = 3):
        self.next = next
        self.state = State()
        self.contact_position = Joints()
        self.count = validation_count
        self.validation_count = validation_count
        self.cmd_id = 0

    def __call__(self, cmd):
        
        self.state[:] = self.next(cmd)
        if not cmd.is_move_command():
            return self.state

        if cmd.id() != self.cmd_id:
            self.cmd_id = cmd.id()
            self.count = self.validation_count
            return self.state

        if self.count == 0:
            self.state.set_state_flag(State._STATUS_FLAG_GOAL_REACHED, 1)
            self.state.set_state_flag(State._STATUS_FLAG_DONE, 1)
            return self.state

        if self.state.is_moving():
            return self.state

        if self.state.is_goal_reached():
            # stopped because the arm reached the goal but didn't detect contact, so this is a failure
            self.state.set_state_flag(State._STATUS_FLAG_GOAL_REACHED, 0)
            self.state.set_state_flag(State._STATUS_FLAG_DONE, 1)
            return self.state

        if not self.state.is_contact():
            return self.state

        if np.allclose(self.contact_position, self.state.joint_positions(), atol=0.01):
            self.count -= 1
        else:
            self.count = self.validation_count
            self.contact_position = self.state.joint_positions()

        return self.state



 