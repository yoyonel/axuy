# view.py - maintain view on game world
# Copyright (C) 2019  Nguyễn Gia Phong
#
# This file is part of Axuy
#
# Axuy is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Axuy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with Axuy.  If not, see <https://www.gnu.org/licenses/>.

__doc__ = 'Axuy module for map class'

from itertools import product
from math import sqrt
from random import choice

import glfw
import moderngl
import numpy as np
from PIL import Image
from pyrr import Matrix44

from .pico import Picobot
from .misc import COLOR_NAMES, abspath, color, neighbors, sign

FOV_MIN = 30
FOV_MAX = 120
FOV_INIT = (FOV_MIN+FOV_MAX) / 2
MOUSE_SPEED = 1/8

OXY = np.float32([[0, 0, 0], [1, 0, 0], [1, 1, 0],
                  [1, 1, 0], [0, 1, 0], [0, 0, 0]])
OYZ = np.float32([[0, 0, 0], [0, 1, 0], [0, 1, 1],
                  [0, 1, 1], [0, 0, 1], [0, 0, 0]])
OZX = np.float32([[0, 0, 0], [1, 0, 0], [1, 0, 1],
                  [1, 0, 1], [0, 0, 1], [0, 0, 0]])

TETRAVERTICES = np.float32([[0, sqrt(8), -1], [sqrt(6), -sqrt(2), -1],
                            [0, 0, 3], [-sqrt(6), -sqrt(2), -1]]) / 12
TETRAINDECIES = np.int32([0, 1, 2, 3, 1, 2, 0, 3, 2, 0, 3, 1])

OCTOVERTICES = np.float32([[-1, 0, 0], [0, -1, 0], [0, 0, -1],
                           [0, 1, 0], [0, 0, 1], [1, 0, 0]]) / 12
OCTOINDECIES = np.int32([0, 1, 2, 0, 1, 4, 3, 0, 2, 3, 0, 4,
                         2, 1, 5, 4, 1, 5, 2, 5, 3, 4, 5, 3])

with open(abspath('shaders/map.vert')) as f: MAP_VERTEX = f.read()
with open(abspath('shaders/map.frag')) as f: MAP_FRAGMENT = f.read()
with open(abspath('shaders/pico.vert')) as f: PICO_VERTEX = f.read()
with open(abspath('shaders/pico.frag')) as f: PICO_FRAGMENT = f.read()


class Pico(Picobot):
    def look(self, window, xpos, ypos):
        """Look according to cursor position.

        Present as a callback for GLFW CursorPos event.
        """
        center = np.float32(glfw.get_window_size(window)) / 2
        self.rotate(*((center - [xpos, ypos]) / self.fps * MOUSE_SPEED))


class View:
    """World map and camera placement.

    Parameters
    ----------
    address : (str, int)
        IP address (host, port).
    camera : Pico
        Protagonist whose view is the camera.
    space : np.ndarray of shape (12, 12, 9) of bools
        3D array of occupied space.
    width, height : ints
        Window size.
    lock : RLock
        Compound data lock to avoid size change during iteration.

    Attributes
    ----------
    addr : (str, int)
        IP address (host, port).
    space : np.ndarray of shape (12, 12, 9) of bools
        3D array of occupied space.
    camera : Pico
        Protagonist whose view is the camera.
    picos : dict of {address: Pico}
        Enemies characters.
    colors : dict of {address: str}
        Color names of enemies.
    lock : RLock
        Compound data lock to avoid size change during iteration.
    window : GLFW window
    fov : int
        horizontal field of view in degrees
    context : moderngl.Context
        OpenGL context from which ModernGL objects are created.
    maprog : moderngl.Program
        Processed executable code in GLSL for map rendering.
    mapva : moderngl.VertexArray
        Vertex data of the map.
    prog : moderngl.Program
        Processed executable code in GLSL
        for rendering picobots and their shards.
    pva : moderngl.VertexArray
        Vertex data of picobots.
    sva : moderngl.VertexArray
        Vertex data of shards.
    last_time : float
        timestamp in seconds of the previous frame.
    """

    def __init__(self, address, camera, space, width, height, lock):
        # Create GLFW window
        if not glfw.init(): raise RuntimeError('Failed to initialize GLFW!')
        glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 3)
        glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 3)
        glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
        glfw.window_hint(glfw.OPENGL_FORWARD_COMPAT, True)
        self.window = glfw.create_window(width, height, 'Axuy', None, None)
        if not self.window:
            glfw.terminate()
            raise RuntimeError('Failed to create GLFW window!')

        self.camera = camera
        self.picos = {address: camera}
        self.colors = {address: choice(COLOR_NAMES)}
        self.last_time = glfw.get_time()
        self.lock = lock

        # Window's rendering and event-handling configuration
        glfw.set_window_icon(self.window, 1, Image.open(abspath('icon.png')))
        glfw.make_context_current(self.window)
        glfw.swap_interval(1)
        glfw.set_input_mode(self.window, glfw.CURSOR, glfw.CURSOR_DISABLED)
        glfw.set_input_mode(self.window, glfw.STICKY_KEYS, True)
        glfw.set_cursor_pos_callback(self.window, self.camera.look)
        self.fov = FOV_INIT
        glfw.set_scroll_callback(self.window, self.zoom)
        glfw.set_mouse_button_callback(self.window, self.shoot)

        # Create OpenGL context
        self.context = context = moderngl.create_context()
        context.enable(moderngl.BLEND)
        context.enable(moderngl.DEPTH_TEST)

        self.space, vertices = space, []
        for (x, y, z), occupied in np.ndenumerate(self.space):
            if self.space[x][y][z-1] ^ occupied:
                vertices.extend(i+j for i,j in product(neighbors(x,y,z), OXY))
            if self.space[x-1][y][z] ^ occupied:
                vertices.extend(i+j for i,j in product(neighbors(x,y,z), OYZ))
            if self.space[x][y-1][z] ^ occupied:
                vertices.extend(i+j for i,j in product(neighbors(x,y,z), OZX))

        self.maprog = context.program(vertex_shader=MAP_VERTEX,
                                      fragment_shader=MAP_FRAGMENT)
        self.maprog['bg'].write(color('Background').tobytes())
        self.maprog['color'].write(color('Aluminium').tobytes())
        mapvb = context.buffer(np.stack(vertices).astype(np.float32).tobytes())
        self.mapva = context.simple_vertex_array(self.maprog, mapvb, 'in_vert')

        self.prog = context.program(vertex_shader=PICO_VERTEX,
                                    fragment_shader=PICO_FRAGMENT)
        pvb = [(context.buffer(TETRAVERTICES.tobytes()), '3f', 'in_vert')]
        pib = context.buffer(TETRAINDECIES.tobytes())
        self.pva = context.vertex_array(self.prog, pvb, pib)
        svb = [(context.buffer(OCTOVERTICES.tobytes()), '3f', 'in_vert')]
        sib = context.buffer(OCTOINDECIES.tobytes())
        self.sva = context.vertex_array(self.prog, svb, sib)

    def zoom(self, window, xoffset, yoffset):
        """Adjust FOV according to vertical scroll."""
        self.fov += yoffset
        if self.fov < FOV_MIN: self.fov = FOV_MIN
        if self.fov > FOV_MAX: self.fov = FOV_MAX

    def shoot(self, window, button, action, mods):
        """Shoot on click.

        Present as a callback for GLFW MouseButton event.
        """
        if button == glfw.MOUSE_BUTTON_LEFT and action == glfw.PRESS:
            self.camera.shoot()

    @property
    def pos(self):
        """Camera position in a NumPy array."""
        return self.camera.pos

    @property
    def right(self):
        """Camera right direction."""
        return self.camera.rot[0]

    @property
    def upward(self):
        """Camera upward direction."""
        return self.camera.rot[1]

    @property
    def forward(self):
        """Camera forward direction."""
        return self.camera.rot[2]

    @property
    def is_running(self) -> bool:
        """GLFW window status."""
        return not glfw.window_should_close(self.window)

    @property
    def visibility(self) -> np.float32:
        """Camera visibility."""
        return np.float32(3240 / (self.fov + 240))

    @property
    def fps(self):
        """Currently rendered frames per second."""
        return self.camera.fps

    @fps.setter
    def fps(self, fps):
        self.camera.fps = fps

    def is_pressed(self, *keys) -> bool:
        """Return whether given keys are pressed."""
        return any(glfw.get_key(self.window, k) == glfw.PRESS for k in keys)

    def render(self, obj, va, col, bright=0):
        """Render the obj and its images in bounded 3D space."""
        vsqr = self.visibility ** 2
        rotation = Matrix44.from_matrix33(obj.rot)
        i, j, k = map(sign, self.pos - obj.pos)
        for position in product(*zip(obj.pos, obj.pos + [i*12, j*12, k*9])):
            if sum((self.pos-position) ** 2) > vsqr: continue
            model = rotation @ Matrix44.from_translation(position)
            self.prog['model'].write(model.astype(np.float32).tobytes())
            self.prog['color'].write(color('Background').tobytes())
            va.render(moderngl.LINES)
            self.prog['color'].write(color(col, bright).tobytes())
            va.render(moderngl.TRIANGLES)

    def render_pico(self, pico):
        """Render pico and its images in bounded 3D space."""
        self.render(pico, self.pva, self.colors[pico.addr])

    def render_shard(self, shard):
        """Render shard and its images in bounded 3D space."""
        self.render(shard, self.sva, self.colors[shard.addr], -shard.power)

    def add_pico(self, address, position, rotation):
        """Add picobot from addr at pos with rot."""
        self.picos[address] = Pico(address, self.space, position, rotation)
        self.colors[address] = choice(COLOR_NAMES)

    def update(self):
        """Handle input, update GLSL programs and render the map."""
        # Update instantaneous FPS
        next_time = glfw.get_time()
        self.fps = 1 / (next_time-self.last_time)
        self.last_time = next_time

        # Character movements
        right, upward, forward = 0, 0, 0
        if self.is_pressed(glfw.KEY_UP): forward += 1
        if self.is_pressed(glfw.KEY_DOWN): forward -= 1
        if self.is_pressed(glfw.KEY_LEFT): right -= 1
        if self.is_pressed(glfw.KEY_RIGHT): right += 1
        self.camera.move(right, upward, forward)

        # Renderings
        width, height = glfw.get_window_size(self.window)
        self.context.viewport = 0, 0, width, height
        self.context.clear(*color('Background'))

        visibility = self.visibility
        projection = Matrix44.perspective_projection(self.fov, width/height,
                                                     3E-3, visibility)
        view = Matrix44.look_at(self.pos, self.pos + self.forward, self.upward)
        vp = (view @ projection).astype(np.float32).tobytes()

        self.maprog['visibility'].write(visibility.tobytes())
        self.maprog['camera'].write(self.pos.tobytes())
        self.maprog['mvp'].write(vp)
        self.mapva.render(moderngl.TRIANGLES)

        self.prog['visibility'].write(visibility.tobytes())
        self.prog['camera'].write(self.pos.tobytes())
        self.prog['vp'].write(vp)

        self.lock.acquire(blocking=False)
        for pico in self.picos.values():
            shards = {}
            for index, shard in pico.shards.items():
                if not shard.power: continue
                shard.update(self.fps)
                self.render_shard(shard)
                shards[index] = shard
            pico.shards = shards
            if pico is not self.camera: self.render_pico(pico)
        self.lock.release()
        glfw.swap_buffers(self.window)

        # Resetting cursor position and event queues
        glfw.set_cursor_pos(self.window, width/2, height/2)
        glfw.poll_events()

    def close(self):
        """Close window."""
        glfw.terminate()
