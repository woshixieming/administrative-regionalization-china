/** Small stable-fluid solver: advection, diffusion and pressure projection. */
export function createInkFluid(canvas: HTMLCanvasElement) {
  // Retain the paper frame between slow updates and when reduced motion pauses rendering.
  const gl = canvas.getContext("webgl2", { alpha: false, antialias: false, depth: false, preserveDrawingBuffer: true });
  if (!gl || !gl.getExtension("EXT_color_buffer_float")) return null;

  const shaders: WebGLShader[] = [];
  const programs: WebGLProgram[] = [];
  const textures: WebGLTexture[] = [];
  const buffers: WebGLFramebuffer[] = [];
  const vertexArray = gl.createVertexArray();
  gl.bindVertexArray(vertexArray);

  const vertex = `#version 300 es
    out vec2 uv;
    void main() {
      vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
      uv = p;
      gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
    }`;
  const common = `#version 300 es
    precision highp float;
    in vec2 uv;
    out vec4 color;
    uniform vec2 texel;
    uniform float dt;
    uniform float time;
    vec4 sampleField(sampler2D field, vec2 p) {
      vec2 q = clamp(p, texel * 0.5, 1.0 - texel * 0.5) / texel - 0.5;
      vec2 base = floor(q), f = fract(q);
      vec2 a = (base + 0.5) * texel;
      return mix(mix(texture(field, a), texture(field, a + vec2(texel.x, 0.0)), f.x),
                 mix(texture(field, a + vec2(0.0, texel.y)), texture(field, a + texel), f.x), f.y);
    }`;

  function shader(type: number, source: string) {
    const value = gl!.createShader(type)!;
    shaders.push(value);
    gl!.shaderSource(value, source);
    gl!.compileShader(value);
    if (!gl!.getShaderParameter(value, gl!.COMPILE_STATUS)) throw new Error("Ink shader unavailable");
    return value;
  }

  function program(fragment: string) {
    const value = gl!.createProgram()!;
    programs.push(value);
    gl!.attachShader(value, shader(gl!.VERTEX_SHADER, vertex));
    gl!.attachShader(value, shader(gl!.FRAGMENT_SHADER, common + fragment));
    gl!.linkProgram(value);
    if (!gl!.getProgramParameter(value, gl!.LINK_STATUS)) throw new Error("Ink program unavailable");
    const uniforms = new Map<string, WebGLUniformLocation | null>();
    return { value, uniform(name: string) {
      if (!uniforms.has(name)) uniforms.set(name, gl!.getUniformLocation(value, name));
      return uniforms.get(name)!;
    } };
  }

  type Program = ReturnType<typeof program>;
  type Target = { texture: WebGLTexture; buffer: WebGLFramebuffer };
  type Pair = { read: Target; write: Target; swap: () => void };
  let width = 0, height = 0;

  function target(): Target {
    const texture = gl!.createTexture()!;
    textures.push(texture);
    gl!.bindTexture(gl!.TEXTURE_2D, texture);
    gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MIN_FILTER, gl!.NEAREST);
    gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_MAG_FILTER, gl!.NEAREST);
    gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_S, gl!.CLAMP_TO_EDGE);
    gl!.texParameteri(gl!.TEXTURE_2D, gl!.TEXTURE_WRAP_T, gl!.CLAMP_TO_EDGE);
    gl!.texImage2D(gl!.TEXTURE_2D, 0, gl!.RGBA16F, width, height, 0, gl!.RGBA, gl!.HALF_FLOAT, null);
    const buffer = gl!.createFramebuffer()!;
    buffers.push(buffer);
    gl!.bindFramebuffer(gl!.FRAMEBUFFER, buffer);
    gl!.framebufferTexture2D(gl!.FRAMEBUFFER, gl!.COLOR_ATTACHMENT0, gl!.TEXTURE_2D, texture, 0);
    if (gl!.checkFramebufferStatus(gl!.FRAMEBUFFER) !== gl!.FRAMEBUFFER_COMPLETE) throw new Error("Ink framebuffer unavailable");
    gl!.clearColor(0, 0, 0, 0);
    gl!.clear(gl!.COLOR_BUFFER_BIT);
    return { texture, buffer };
  }

  function pair(): Pair {
    return { read: target(), write: target(), swap() { [this.read, this.write] = [this.write, this.read]; } };
  }

  function bind(p: Program, name: string, field: Target, unit: number) {
    gl!.activeTexture(gl!.TEXTURE0 + unit);
    gl!.bindTexture(gl!.TEXTURE_2D, field.texture);
    gl!.uniform1i(p.uniform(name), unit);
  }

  function activateProgram(p: Program, step = 1 / 30, time = 0) {
    gl!.useProgram(p.value);
    gl!.uniform2f(p.uniform("texel"), 1 / width, 1 / height);
    gl!.uniform1f(p.uniform("dt"), step);
    gl!.uniform1f(p.uniform("time"), time);
  }

  function draw(destination?: Target) {
    gl!.bindFramebuffer(gl!.FRAMEBUFFER, destination?.buffer ?? null);
    gl!.viewport(0, 0, destination ? width : canvas.width, destination ? height : canvas.height);
    gl!.drawArrays(gl!.TRIANGLES, 0, 3);
  }

  function clearTargets() {
    for (const value of textures.splice(0)) gl!.deleteTexture(value);
    for (const value of buffers.splice(0)) gl!.deleteFramebuffer(value);
  }

  const dispose = () => {
    clearTargets();
    programs.forEach((p) => gl.deleteProgram(p));
    shaders.forEach((s) => gl.deleteShader(s));
    gl.deleteVertexArray(vertexArray);
  };

  try {
    const splat = program(`
      uniform sampler2D field;
      uniform vec2 point;
      uniform vec4 pigment;
      uniform float aspect;
      uniform float radius;
      void main() {
        vec2 p = uv - point;
        p.x *= aspect;
        float edge = 1.0 + 0.17 * sin(p.x * 51.0 + p.y * 37.0) * sin(p.y * 43.0 - p.x * 29.0);
        float shape = exp(-dot(p, p) / (radius * edge));
        color = texture(field, uv) + pigment * shape;
      }`);
    const advect = program(`
      uniform sampler2D velocity;
      uniform sampler2D field;
      uniform float retention;
      void main() {
        vec2 previous = uv - dt * texture(velocity, uv).xy;
        color = sampleField(field, previous) * retention;
      }`);
    const ambient = program(`
      uniform sampler2D velocity;
      void main() {
        vec2 p = uv * 6.283185;
        vec2 current = vec2(sin(p.y * 1.4 + time * 0.18), cos(p.x * 1.2 - time * 0.14));
        // A second divergence-free current folds the ink instead of only translating it.
        vec2 phase = vec2(p.x * 1.8 + time * 0.12, p.y * 1.6 - time * 0.10);
        current += 0.24 * vec2(1.6 * sin(phase.x) * cos(phase.y), -1.8 * cos(phase.x) * sin(phase.y));
        color = vec4(texture(velocity, uv).xy * exp(-dt * 0.18) + current * dt * 0.0075, 0.0, 1.0);
      }`);
    const divergence = program(`
      uniform sampler2D velocity;
      void main() {
        float l = texture(velocity, uv - vec2(texel.x, 0.0)).x;
        float r = texture(velocity, uv + vec2(texel.x, 0.0)).x;
        float b = texture(velocity, uv - vec2(0.0, texel.y)).y;
        float t = texture(velocity, uv + vec2(0.0, texel.y)).y;
        color = vec4(0.5 * (r - l + t - b), 0.0, 0.0, 1.0);
      }`);
    const pressure = program(`
      uniform sampler2D pressure;
      uniform sampler2D divergence;
      void main() {
        float l = texture(pressure, uv - vec2(texel.x, 0.0)).x;
        float r = texture(pressure, uv + vec2(texel.x, 0.0)).x;
        float b = texture(pressure, uv - vec2(0.0, texel.y)).x;
        float t = texture(pressure, uv + vec2(0.0, texel.y)).x;
        color = vec4((l + r + b + t - texture(divergence, uv).x) * 0.25, 0.0, 0.0, 1.0);
      }`);
    const project = program(`
      uniform sampler2D velocity;
      uniform sampler2D pressure;
      void main() {
        float l = texture(pressure, uv - vec2(texel.x, 0.0)).x;
        float r = texture(pressure, uv + vec2(texel.x, 0.0)).x;
        float b = texture(pressure, uv - vec2(0.0, texel.y)).x;
        float t = texture(pressure, uv + vec2(0.0, texel.y)).x;
        vec2 v = texture(velocity, uv).xy - 0.5 * vec2(r - l, t - b);
        if (uv.x < texel.x || uv.x > 1.0 - texel.x) v.x = 0.0;
        if (uv.y < texel.y || uv.y > 1.0 - texel.y) v.y = 0.0;
        color = vec4(v, 0.0, 1.0);
      }`);
    const diffuse = program(`
      uniform sampler2D field;
      void main() {
        vec4 c = texture(field, uv);
        vec4 adjacent = texture(field, uv + vec2(texel.x, 0.0)) + texture(field, uv - vec2(texel.x, 0.0))
          + texture(field, uv + vec2(0.0, texel.y)) + texture(field, uv - vec2(0.0, texel.y));
        color = mix(c, adjacent * 0.25, 0.025);
      }`);
    const display = program(`
      uniform sampler2D ink;
      uniform vec2 resolution;
      float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float noise(vec2 p) {
        vec2 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + 1.0), f.x), f.y);
      }
      float absorption(vec2 p) { return dot(sampleField(ink, p), vec4(0.13, 0.23, 0.36, 0.52)); }
      void main() {
        vec2 pixel = uv * resolution;
        float grain = noise(pixel * 0.8) - 0.5;
        float fiber = noise(pixel * vec2(0.035, 0.6)) - 0.5;
        float pulp = noise(pixel * 0.009) - 0.5;
        vec3 paper = vec3(0.973, 0.961, 0.933) + grain * 0.016 + fiber * 0.012 + pulp * 0.009;
        float inkAmount = absorption(uv);
        vec2 edge = vec2(absorption(uv + vec2(texel.x, 0.0)) - absorption(uv - vec2(texel.x, 0.0)),
                         absorption(uv + vec2(0.0, texel.y)) - absorption(uv - vec2(0.0, texel.y)));
        float halo = length(edge) * 1.35;
        // Beer–Lambert absorption: all four pigments are black; paper multiplies transmission.
        color = vec4(paper * exp(-(inkAmount + halo) * (0.82 + pulp * 0.12)), 1.0);
      }`);

    let velocity: Pair, ink: Pair, pressures: Pair, divergenceField: Target;
    let simulationTime = 0;

    function seed() {
      // One jittered point per cell in a 4 × 3 grid avoids clustered random drops.
      let randomState = 731;
      const random = () => { randomState = (randomState * 16807) % 2147483647; return randomState / 2147483647; };
      for (let i = 0; i < 12; i++) {
        const x = (i % 4 + 0.25 + random() * 0.5) / 4;
        const y = (Math.floor(i / 4) + 0.25 + random() * 0.5) / 3;
        const angle = random() * Math.PI * 2;
        activateProgram(splat);
        gl!.uniform2f(splat.uniform("point"), x, y);
        gl!.uniform1f(splat.uniform("aspect"), width / height);
        gl!.uniform1f(splat.uniform("radius"), 0.0045 + random() * 0.007);
        const pigment = [0, 0, 0, 0];
        pigment[i % 4] = 0.55 + random() * 0.7;
        gl!.uniform4fv(splat.uniform("pigment"), pigment);
        bind(splat, "field", ink.read, 0); draw(ink.write); ink.swap();
        gl!.uniform4f(splat.uniform("pigment"), Math.cos(angle) * 0.035, Math.sin(angle) * 0.035, 0, 0);
        bind(splat, "field", velocity.read, 0); draw(velocity.write); velocity.swap();
      }
    }

    function resize() {
      const box = canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.max(1, Math.round(box.width * ratio));
      canvas.height = Math.max(1, Math.round(box.height * ratio));
      const aspect = box.width / Math.max(box.height, 1);
      const longest = box.width < 640 ? 160 : 224;
      width = Math.max(64, Math.round(aspect >= 1 ? longest : longest * aspect));
      height = Math.max(64, Math.round(aspect >= 1 ? longest / aspect : longest));
      clearTargets();
      velocity = pair(); ink = pair(); pressures = pair(); divergenceField = target();
      simulationTime = 0;
      seed();
    }

    function step(dt = 1 / 30) {
      simulationTime += dt;
      activateProgram(advect, dt); bind(advect, "velocity", velocity.read, 0); bind(advect, "field", velocity.read, 1);
      gl!.uniform1f(advect.uniform("retention"), 0.998); draw(velocity.write); velocity.swap();
      activateProgram(ambient, dt, simulationTime); bind(ambient, "velocity", velocity.read, 0); draw(velocity.write); velocity.swap();
      activateProgram(divergence); bind(divergence, "velocity", velocity.read, 0); draw(divergenceField);
      activateProgram(pressure); bind(pressure, "divergence", divergenceField, 1);
      for (let i = 0; i < 10; i++) { bind(pressure, "pressure", pressures.read, 0); draw(pressures.write); pressures.swap(); }
      activateProgram(project); bind(project, "velocity", velocity.read, 0); bind(project, "pressure", pressures.read, 1);
      draw(velocity.write); velocity.swap();
      activateProgram(advect, dt); bind(advect, "velocity", velocity.read, 0); bind(advect, "field", ink.read, 1);
      gl!.uniform1f(advect.uniform("retention"), 1); draw(ink.write); ink.swap();
      activateProgram(diffuse); bind(diffuse, "field", ink.read, 0); draw(ink.write); ink.swap();
    }

    function render() {
      activateProgram(display); bind(display, "ink", ink.read, 0);
      gl!.uniform2f(display.uniform("resolution"), canvas.width, canvas.height);
      draw();
    }

    return { resize, step, render, dispose };
  } catch {
    dispose();
    return null;
  }
}
