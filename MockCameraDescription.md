# Mock Camera Image Description

The mock camera does not generate a flat test pattern. It synthesizes a fluorescence-style scene: a bright sensor background, a fixed Gaussian illumination spot, a population of small irregular particles that shimmer and drift, and four square fiducials that move with the sample.

## Default frame shape and sensor style

- Image size: `2048 x 2048`
- Bit depth: `16-bit unsigned`
- Default camera mode: continuous, internally triggered
- Default visual character: bright grayscale image with a softly illuminated center and darker edges

At rest, the frame starts from a nonzero background floor of about `10000` counts. On top of that, the mock adds a circular Gaussian illumination field centered in the image. With the default settings, that illumination peak is `30%` of the full 16-bit range, so the middle of the frame is noticeably brighter than the corners even before any particles are added.

## What the scene looks like

The easiest mental model is a microscope view of fluorescent material under a centered excitation beam:

- The middle of the image is brightest because the illumination profile is a 2-D Gaussian centered at `50%` width and `50%` height.
- Brightness rolls off smoothly toward the edges rather than ending abruptly.
- The image contains about `20` small bright objects by default, plus one extra reference object near the upper-left area.
- Each object is not a perfect circle. The code builds each one as a filled irregular polygon with roughly `5` to `8` vertices, so they look like tiny uneven blobs rather than clean dots.
- Four small square fiducials sit around the image center, arranged symmetrically in a rectangular pattern.

So visually, the default frame is not sparse black-on-white or white-on-black. It is a bright, noisy grayscale field with a broad central glow, mottled bright specks, and four crisp geometric markers near the center region.

## Particles

The particles are the main textured content in the image.

By default:

- Particle count: `20`
- Radius range: about `2` to `4` pixels before blur
- Mean base intensity: `0.15` of sensor full scale
- Per-particle intensity spread: standard deviation `0.10`
- Per-particle oscillation amplitude: `0.35` of that particle's base signal

Important visual details:

- Particle positions are randomized when the particle set is regenerated.
- Their shapes are randomized too, so different particles do not all look alike.
- Each particle has its own oscillation period and phase, so the field twinkles asynchronously.
- Particles are weighted by the local illumination intensity. A particle near the center of the Gaussian beam appears brighter than an equally sized particle near the edge.

This means the image usually looks densest and most active near the beam center. Toward the edges, particles are still present, but they tend to be dimmer because the excitation profile falls off.

## Extra reference particle

In addition to the randomized particle population, the mock inserts one extra particle near the upper-left margin. It is more deterministic than the others:

- It is placed near the top-left side rather than at a random interior location.
- Its oscillation period is fixed.
- Its phase is fixed.

That gives the scene a semi-stable bright reference feature away from the central cluster.

## Fiducials

The fiducials are separate from the particle field.

By default:

- Enabled: yes
- Size: `6 px` squares
- Offset from image center: `20%` of the image width and height
- Intensity: `60%` of full scale

They form four bright square markers centered around the midpoint of the frame, one in each quadrant relative to the center. With the default settings on a `2048 x 2048` image, they sit roughly at:

- upper-left of center
- upper-right of center
- lower-left of center
- lower-right of center

Because they are drawn as filled rectangles, they read as more geometric and deliberate than the organic particle blobs. They are useful as obvious alignment landmarks.

## Blur and focus

The image is blurred with a Gaussian filter to simulate focus and defocus.

Default focus setting:

- `focus_sigma = 0.8 px`

With that default, the image is mildly softened rather than perfectly sharp. The particles look slightly spread, and the fiducials have edges that are softened a bit instead of being pixel-hard.

If the Z stage is moved, the mock increases blur further. Defocus is applied to:

- the particle layer
- the fiducial layer
- the illumination field itself

So when Z moves away from focus, the whole scene looks softer and the central beam appears more spread out.

## Motion over time

The mock image is animated. The frame is not just noisy; multiple motion sources are layered together.

### 1. Sample drift

Drift is enabled by default.

- Drift amplitude: `20 px`
- Drift speed: `0.5`

The drift is sinusoidal in both X and Y, but the two axes use different effective periods and phases. That makes the sample wander in a looping, not perfectly circular, path. The particles and fiducials move together under this drift, so they feel attached to the same sample.

### 2. Manual translation

The mock camera also supports explicit X and Y translation offsets. These are added on top of the sinusoidal drift. Visually, this shifts the particle field and fiducials together as one sample.

### 3. Stage-linked motion

If the app has a shared stage object, stage X and Y positions also shift the sample in image space. Z position adds extra blur. The illumination profile does not move with the sample, so moving the sample through the fixed beam changes which particles are brightly excited.

### 4. Per-particle oscillation

Each particle has its own brightness oscillation. This produces a local shimmering effect where individual blobs brighten and dim at different rates.

### 5. Global pulse

On top of the per-particle oscillation, the entire particle population shares a slower global brightness pulse.

Default pulse settings:

- Period: `3.0 s`
- Amplitude: `0.5`

This makes the whole particle field breathe brighter and dimmer over time. The fiducials do not participate in this global pulse, so they remain comparatively steady while the particles fluctuate.

## What moves and what stays fixed

This is one of the most important visual distinctions in the mock image.

Fixed in image coordinates:

- the Gaussian illumination field
- the underlying sensor frame/background

Moves with the sample:

- all particles
- the four fiducial squares
- manual translation offsets
- simulated drift offsets
- stage X/Y motion

Affected by the global pulse:

- particles

Not affected by the global pulse:

- fiducials
- background floor
- illumination field

That design makes the scene look like a sample moving through a stationary excitation beam instead of the beam moving with the sample.

## Noise and realism

After the deterministic image is assembled, the mock applies Poisson shot noise. This gives the frame a grainy sensor-like appearance rather than smooth synthetic gradients.

Practical visual result:

- smooth areas still flicker slightly frame to frame
- dim regions look noisier relative to their signal
- bright regions remain bright but are not perfectly stable

The final frame is clipped to the active sensor range, so very bright overlaps can saturate near the top of the bit depth.

## Overall visual summary

With default settings, the mock camera produces a grayscale microscope-like scene with these dominant characteristics:

- a bright, broad central illumination glow
- darker corners due to Gaussian falloff
- around twenty small, irregular, softly blurred fluorescent blobs
- one extra blob near the upper-left region
- four bright square fiducials arranged around the center
- slow sample drift and optional translation
- particle twinkling plus a slower whole-field breathing pulse
- mild shot noise across the frame

If you had to describe the default image in one sentence: it looks like a noisy fluorescence microscopy field where a drifting sample full of small blinking emitters passes through a stationary Gaussian laser spot, with four square registration marks near the center.