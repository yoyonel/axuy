#version 330

uniform vec3 bg;
uniform vec3 color;

in float intensity;
out vec4 f_color;

void main() {
	f_color = vec4(bg * (1 - intensity) + color * intensity, 1.0);
}
