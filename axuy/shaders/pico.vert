#version 330

uniform mat4 vp;
uniform mat4 model;
uniform vec3 camera;
uniform float visibility;

in vec3 in_vert;
out float depth;

void main() {
	vec4 vert = model * vec4(in_vert, 1.0);
	gl_Position = vp * vert;
	depth = distance(camera, vec3(vert)) / visibility;
}
