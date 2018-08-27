"""Sawyer Interface."""
from geometry_msgs.msg import Pose
import gym
from intera_core_msgs.msg import JointLimits
import intera_interface
import moveit_msgs.msg
import numpy as np
import rospy

from garage.contrib.ros.robots.kinematics_interfaces import StateValidity
from garage.contrib.ros.robots.robot import Robot


class Sawyer(Robot):
    """Sawyer class."""

    def __init__(self,
                 initial_joint_pos,
                 moveit_group,
                 control_mode='position'):
        """
        Sawyer class.

        :param initial_joint_pos: {str: float}
                        {'joint_name': position_value}, and also
                        initial_joint_pos should include all of the
                        joints that user wants to control and observe.
        :param moveit_group: str
                        Use this to check safety
        :param control_mode: string
                        robot control mode: 'position' or velocity
                        or effort
        """
        Robot.__init__(self)
        self._limb = intera_interface.Limb('right')
        self._gripper = intera_interface.Gripper()
        self._initial_joint_pos = initial_joint_pos
        self._control_mode = control_mode
        self._used_joints = []
        for joint in initial_joint_pos:
            self._used_joints.append(joint)
        self._joint_limits = rospy.wait_for_message('/robot/joint_limits',
                                                    JointLimits)
        self._moveit_group = moveit_group

        self._sv = StateValidity()

    def safety_check(self):
        """
        If robot is in safe state.

        :return safe: Bool
                if robot is safe.
        """
        rs = moveit_msgs.msg.RobotState()
        current_joint_angles = self._limb.joint_angles()
        for joint in current_joint_angles:
            rs.joint_state.name.append(joint)
            rs.joint_state.position.append(current_joint_angles[joint])
        result = self._sv.get_state_validity(rs, self._moveit_group)
        return result.valid

    def safety_predict(self, joint_angles):
        """
        Will robot be in safe state.

        :param joint_angles: {'': float}
        :return safe: Bool
                    if robot is safe.
        """
        rs = moveit_msgs.msg.RobotState()
        for joint in joint_angles:
            rs.joint_state.name.append(joint)
            rs.joint_state.position.append(joint_angles[joint])
        result = self._sv.get_state_validity(rs, self._moveit_group)
        return result.valid

    def move_gripper_to_position(self, position):
        desired_pose = Pose()
        current_pose = self._limb.endpoint_pose()
        desired_pose.orientation.w = current_pose['orientation'].w
        desired_pose.orientation.x = current_pose['orientation'].x
        desired_pose.orientation.y = current_pose['orientation'].y
        desired_pose.orientation.z = current_pose['orientation'].z
        desired_pose.position.x = position[0]
        desired_pose.position.y = position[1]
        desired_pose.position.z = position[2]
        
        joint_angles = self._limb.ik_request(desired_pose, "right_hand")
        self._limb.move_to_joint_positions(joint_angles)

    @property
    def enabled(self):
        """
        If robot is enabled.

        :return: if robot is enabled.
        """
        return intera_interface.RobotEnable(
            intera_interface.CHECK_VERSION).state().enabled

    def _set_limb_joint_positions(self, joint_angle_cmds):
        # limit joint angles cmd
        current_joint_angles = self._limb.joint_angles()
        for joint in joint_angle_cmds:
            joint_cmd_delta = joint_angle_cmds[joint] - \
                              current_joint_angles[joint]
            joint_angle_cmds[
                joint] = current_joint_angles[joint] + joint_cmd_delta * 0.1

        if self.safety_predict(joint_angle_cmds):
            self._limb.set_joint_positions(joint_angle_cmds)

    def _set_limb_joint_velocities(self, joint_angle_cmds):
        self._limb.set_joint_velocities(joint_angle_cmds)

    def _set_limb_joint_torques(self, joint_angle_cmds):
        self._limb.set_joint_torques(joint_angle_cmds)

    def _set_gripper_position(self, position):
        self._gripper.set_position(position)


    def _move_to_start_position(self):
        if rospy.is_shutdown():
            return
        self._limb.move_to_joint_positions(
            self._initial_joint_pos, timeout=5.0)
        self._gripper.open()
        rospy.sleep(0.01)

    def reset(self):
        """Reset sawyer."""
        self._move_to_start_position()

    def get_observation(self):
        """
        Get robot observation.

        :return: robot observation
        """
        # cartesian space
        gripper_pos = np.array(self._limb.endpoint_pose()['position'])
        gripper_ori = np.array(self._limb.endpoint_pose()['orientation'])
        gripper_lvel = np.array(self._limb.endpoint_velocity()['linear'])
        gripper_avel = np.array(self._limb.endpoint_velocity()['angular'])
        gripper_force = np.array(self._limb.endpoint_effort()['force'])
        gripper_torque = np.array(self._limb.endpoint_effort()['torque'])

        # joint space
        robot_joint_angles = np.array(list(self._limb.joint_angles().values()))
        robot_joint_velocities = np.array(
            list(self._limb.joint_velocities().values()))
        robot_joint_efforts = np.array(
            list(self._limb.joint_efforts().values()))

        obs = np.concatenate(
            (gripper_pos, gripper_ori, gripper_lvel, gripper_avel,
             gripper_force, gripper_torque, robot_joint_angles,
             robot_joint_velocities, robot_joint_efforts))
        return obs

    @property
    def limb_joint_angles(self):
        return self._limb.joint_angles()

    @property
    def gripper_position(self):
        return np.array(self._limb.endpoint_pose()['position'])
    

    @property
    def observation_space(self):
        """
        Observation space.

        :return: gym.spaces
                    observation space
        """
        return gym.spaces.Box(
            -np.inf,
            np.inf,
            shape=self.get_observation().shape,
            dtype=np.float32)

    @property
    def joint_position_space(self):
        low = np.array(
            [-3.0503, -3.8095, -3.0426, -3.0439, -2.9761, -2.9761, -4.7124])
        high = np.array(
            [3.0503, 2.2736, 3.0426, 3.0439, 2.9761, 2.9761, 4.7124])
        return gym.spaces.Box(low, high, dtype=np.float32)

    def _send_incremental_position_command(self, jpos):
        current_joint_angles = self._limb.joint_angles()
        joint_limits = self.joint_position_space
        commands = {}

        for j, p in current_joint_angles.items():
            index = int(j[-1])
            commands[j] = np.clip(p + jpos[j], joint_limits.low[index], joint_limits.high[index])
        self._limb.set_joint_positions(commands)

    def send_command(self, commands):
        """
        Send command to sawyer.

        :param commands: [float]
                    list of command for different joints and gripper
        """
        action_space = self.action_space
        commands = np.clip(commands, action_space.low, action_space.high)
        i = 0
        joint_commands = {}
        for joint in self._used_joints:
            joint_commands[joint] = commands[i]
            i += 1

        if self._control_mode == 'position':
            self._send_incremental_position_command(joint_commands)
        elif self._control_mode == 'velocity':
            self._set_limb_joint_velocities(joint_commands)
        elif self._control_mode == 'effort':
            self._set_limb_joint_torques(joint_commands)

        # self._set_gripper_position(commands[7])

    @property
    def gripper_pose(self):
        """
        Get the gripper pose.

        :return: gripper pose
        """
        return self._limb.endpoint_pose()

    @property
    def action_space(self):
        """
        Return a Space object.

        :return: action space
        """
        for joint in self._used_joints:
            joint_idx = self._joint_limits.joint_names.index(joint)
            if self._control_mode == 'position':
                return gym.spaces.Box(low=np.full(7, -0.02), high=np.full(7, 0.02), dtype=np.float32)
            elif self._control_mode == 'velocity':
                velocity_limit = np.array(
                    self._joint_limits.velocity[joint_idx:joint_idx + 1]) * 0.1
                lower_bounds = np.concatenate((lower_bounds, -velocity_limit))
                upper_bounds = np.concatenate((upper_bounds, velocity_limit))
            elif self._control_mode == 'effort':
                effort_limit = np.array(
                    self._joint_limits.effort[joint_idx:joint_idx + 1])
                lower_bounds = np.concatenate((lower_bounds, -effort_limit))
                upper_bounds = np.concatenate((upper_bounds, effort_limit))
            else:
                raise ValueError(
                    'Control mode %s is not known!' % self._control_mode)
        return gym.spaces.Box(
            np.concatenate((lower_bounds, np.array([0]))),
            np.concatenate((upper_bounds, np.array([100]))),
            dtype=np.float32)

    @property
    def joint_positions(self):
        current_positions = self._limb.joint_angles()
        jpos = [current_positions["right_j{}".format(i)] for i in range(7)]
        return jpos

