'use client';

import { useRef, useEffect, useCallback, ComponentType } from 'react';

// =============================================================================
// CONSTANTS — tune these to dial in the feel
// =============================================================================

// --- Tracking spring: how the pill follows your finger while dragging ---
// Higher TRACKING_DAMPING → more lag and overshoot on direction changes (heavier feel).
// Lower → snappier but can feel twitchy. Range: 0.5–0.85.
const TRACKING_DAMPING   = 0.72;
// Higher TRACKING_STIFFNESS → pill snaps to finger faster (less lag).
// Lower → more lag, more overshoot. Range: 0.05–0.20.
const TRACKING_STIFFNESS = 0.10;

// --- Deformation spring: squash-and-stretch wobble while dragging ---
// Higher DEFORM_DAMPING → shape settles quickly (less wobble after direction change).
// Lower → longer, bouncier wobble. Range: 0.70–0.90.
const DEFORM_DAMPING   = 0.88;
// How strongly the deformation spring pulls back to neutral each frame.
// Higher → shape recovers faster. Range: 0.02–0.10.
const DEFORM_STIFFNESS = 0.02;
// Maximum deformation value. 1.0 = fully wide/flat, -1.0 = fully narrow/tall.
const DEFORM_MAX = 1.0;
// How much display velocity drives deformation each frame.
// Higher → shape responds more aggressively to movement speed. Range: 0.10–0.30.
const DEFORM_DRIVE = 0.18;

// --- Scale lift: pill grows on touch, shrinks on release ---
// How fast scale rises toward 1.0 each frame (during press/drag).
// 0.12 ≈ reaches ~95% in 25 frames (~400ms at 60fps). Range: 0.08–0.18.
const SCALE_RISE_RATE = 0.12;
// Extra width added to each side at full scale, as fraction of pill width.
// 0.10 = 10% each side → ~1.2× wider at peak. Range: 0.06–0.14.
const SCALE_WIDTH_FACTOR  = 0.10;
// Extra height added to each side at full scale, as fraction of pill height.
// 0.20 = 20% each side → ~1.4× taller at peak. Range: 0.12–0.24.
const SCALE_HEIGHT_FACTOR = 0.20;

// --- Deformation redistribution: asymmetric expansion gives direction cues ---
// Fraction of width budget shifted toward the leading edge when wide.
// 0.40 = leading side gets 40% more. Range: 0.20–0.50.
const LEAD_SHIFT_FRACTION    = 0.40;
// Fraction of width budget removed from both sides during narrow/rebound phase.
// Range: 0.20–0.40.
const NARROW_SHRINK_FRACTION = 0.30;
// Fraction of height budget added as squash when pill is wide.
// Range: 0.30–0.60.
const SQUASH_FRACTION  = 0.50;
// Fraction of height budget added as stretch when pill is narrow.
// Range: 0.25–0.50.
const STRETCH_FRACTION = 0.40;

// --- Snap/click spring: animates pill from current pos → target tab ---
// Higher SNAP_DAMPING → less bounce, more linear. Lower → springy overshoot.
// Range: 0.68–0.80.
const SNAP_DAMPING   = 0.72;
// Higher SNAP_STIFFNESS → faster travel. Lower → slow, lazy. Range: 0.018–0.045.
const SNAP_STIFFNESS = 0.028;
// Max leading-edge stretch in px during snap.
const SNAP_MAX_STRETCH_PX = 14;
// Max squash in px perpendicular to motion during snap.
const SNAP_MAX_SQUASH_PX  = 2;
// Scale rise rate while traveling toward target.
const SNAP_SCALE_RISE = 0.14;
// Scale fall rate once deceleration/proximity detected.
const SNAP_SCALE_FALL = 0.18;
// Scale below this → snap is done, restore idle CSS transition.
const SNAP_SCALE_EXIT_THRESHOLD = 0.02;
// Within this many px of target → trigger scale-down (fallback for short snaps
// where speed never builds, preventing an infinite hover).
const SNAP_NEAR_TARGET_PX = 2.0;
// Scale-down also triggers once speed drops below this fraction of peak speed.
const SNAP_PEAK_VELOCITY_FALLOFF = 0.70;

// --- Bounce: scale pulse when tapping the already-selected tab ---
// Peak scale value. Lower = subtler. Range: 0.4–0.8.
const BOUNCE_PEAK           = 0.6;
const BOUNCE_RISE_RATE      = 0.14;
const BOUNCE_FALL_RATE      = 0.16;
const BOUNCE_EXIT_THRESHOLD = 0.02;

// --- Rubber band: resistance at first/last tab edges ---
const RUBBER_BAND_MAX_PX = 14;
const RUBBER_BAND_DECAY  = 50;

// --- Time-delta simulation ---
// Target frame duration. Spring constants are tuned for this step size.
// On slow devices (30fps), the RAF runs multiple steps per frame to match
// the same wall-clock animation duration as a 60fps device.
const FIXED_STEP_MS = 1000 / 60; // ~16.67 ms
// Safety cap: never simulate more than this many steps per RAF to avoid
// the "spiral of death" on a tab that regains focus after being hidden.
const MAX_STEPS_PER_FRAME = 4;

// --- Drag gesture ---
// Horizontal movement (px) before a press becomes a drag. Range: 3–6.
const DRAG_THRESHOLD_PX   = 4;
const VELOCITY_EMA_WEIGHT = 0.45;
const VELOCITY_MAX_DT_MS  = 100;
// Lookahead (ms) for projecting position on fling. Range: 60–120.
const FLING_LOOKAHEAD_MS  = 80;

// CSS spring for idle state (external nav / resize / back-forward).
const CSS_SPRING = 'transform 380ms cubic-bezier(0.34, 1.56, 0.64, 1), width 380ms cubic-bezier(0.34, 1.3, 0.64, 1), height 380ms cubic-bezier(0.34, 1.3, 0.64, 1), opacity 300ms ease-out';

// =============================================================================
// TYPES
// =============================================================================

// Animation phase — what the RAF loop is currently doing.
type AnimPhase =
  | { tag: 'idle' }
  | { tag: 'pressed' }                    // same-tab hold: scale rises in place
  | { tag: 'dragging' }                   // following finger
  | { tag: 'snapping'; targetIndex: number }
  | { tag: 'bounce';   tabIndex: number };

// Active gesture — the current pointer interaction, independent of animation.
// Null when no finger/mouse is down.
type Gesture = {
  pointerId: number;
  startX: number;          // clientX at pointerdown
  startPillX: number;      // pill center to use as drag anchor
  clickedIndex: number;
  wasAlreadySelected: boolean;
  hasDragged: boolean;     // crossed DRAG_THRESHOLD_PX
};

type TabLayout = { centerX: number; width: number };

export interface NavPillProps {
  tabs: { page: string; label: string; Icon: ComponentType<{ size: number }> }[];
  selectedIndex: number;
  onSelect: (index: number) => void;
}

// =============================================================================
// PURE HELPERS — module-level, never recreated
// =============================================================================

function getMorphedWidth(centerX: number, layouts: TabLayout[]): number {
  if (layouts.length === 0) return 0;
  if (centerX <= layouts[0].centerX) return layouts[0].width;
  if (centerX >= layouts[layouts.length - 1].centerX) return layouts[layouts.length - 1].width;
  for (let i = 0; i < layouts.length - 1; i++) {
    if (centerX >= layouts[i].centerX && centerX <= layouts[i + 1].centerX) {
      const t = (centerX - layouts[i].centerX) / (layouts[i + 1].centerX - layouts[i].centerX);
      return layouts[i].width + t * (layouts[i + 1].width - layouts[i].width);
    }
  }
  return layouts[0].width;
}

function findNearestIndex(centerX: number, layouts: TabLayout[]): number {
  let best = 0, bestDist = Infinity;
  for (let i = 0; i < layouts.length; i++) {
    const d = Math.abs(centerX - layouts[i].centerX);
    if (d < bestDist) { bestDist = d; best = i; }
  }
  return best;
}

function applyRubberBand(rawX: number, layouts: TabLayout[]): number {
  if (layouts.length === 0) return rawX;
  const first = layouts[0].centerX;
  const last  = layouts[layouts.length - 1].centerX;
  if (rawX < first) return first - RUBBER_BAND_MAX_PX * (1 - Math.exp(-(first - rawX) / RUBBER_BAND_DECAY));
  if (rawX > last)  return last  + RUBBER_BAND_MAX_PX * (1 - Math.exp(-(rawX  - last)  / RUBBER_BAND_DECAY));
  return rawX;
}

function projectWithVelocity(centerX: number, velPxPerMs: number): number {
  return centerX + velPxPerMs * FLING_LOOKAHEAD_MS;
}

// =============================================================================
// COMPONENT
// =============================================================================

export function NavPill({ tabs, selectedIndex, onSelect }: NavPillProps) {

  // --- DOM refs ---
  const containerRef      = useRef<HTMLDivElement>(null);
  const overlayRef        = useRef<HTMLDivElement>(null);
  const overlayContentRef = useRef<HTMLDivElement>(null);
  const tabButtonRefs     = useRef<(HTMLButtonElement | null)[]>([]);

  // --- Layout cache (ResizeObserver only — never touched in RAF) ---
  const tabLayoutsRef    = useRef<TabLayout[]>([]);
  const naturalInsetsRef = useRef<{ top: number; bottom: number }>({ top: 0, bottom: 0 });

  // --- Animation phase (what the RAF is rendering) ---
  const animPhaseRef     = useRef<AnimPhase>({ tag: 'idle' });
  const selectedIndexRef = useRef<number>(selectedIndex);
  useEffect(() => { selectedIndexRef.current = selectedIndex; }, [selectedIndex]);

  // --- Active gesture (pointer tracking, independent of animation) ---
  const gestureRef = useRef<Gesture | null>(null);

  // --- RAF handle ---
  const rafIdRef = useRef<number | null>(null);

  // --- Unified position spring (used by ALL animated phases) ---
  // displayCenterRef is always the current visual pill center — never goes stale.
  const displayCenterRef    = useRef<number>(0);
  const displayCenterVelRef = useRef<number>(0);
  // Target for the dragging spring — written by onPointerMove each frame.
  const desiredCenterRef    = useRef<number>(0);

  // --- Deformation spring (drag only) ---
  const deformRef    = useRef<number>(0);
  const deformVelRef = useRef<number>(0);

  // --- Scale lift (all animated phases) ---
  const scaleFRef = useRef<number>(0);

  // --- Snap-phase tracking ---
  const peakSnapSpeedRef = useRef<number>(0);
  const scalingDownRef   = useRef<boolean>(false);

  // --- Bounce-phase tracking ---
  const bounceRisingRef = useRef<boolean>(true);

  // --- Time-delta tracking ---
  const lastRafTimestampRef = useRef<number>(-1);

  // --- Pointer velocity (for fling projection on release) ---
  const pointerVelRef   = useRef<number>(0); // px/ms, EMA
  const lastPointerXRef = useRef<number>(0);
  const lastPointerTRef = useRef<number>(0);

  // ===========================================================================
  // Layout cache — the ONLY place getBoundingClientRect is called
  // ===========================================================================

  const recomputeLayouts = useCallback(() => {
    const container = containerRef.current;
    const buttons   = tabButtonRefs.current;
    if (!container || buttons.length === 0 || buttons.some(b => b === null)) return;

    const cRect   = container.getBoundingClientRect();
    const layouts = buttons.map(btn => {
      const r = btn!.getBoundingClientRect();
      return { centerX: r.left - cRect.left + r.width / 2, width: r.width };
    });
    tabLayoutsRef.current = layouts;

    const fb = buttons[0]!.getBoundingClientRect();
    naturalInsetsRef.current = { top: fb.top - cRect.top, bottom: cRect.bottom - fb.bottom };

    if (animPhaseRef.current.tag === 'idle') {
      writeOverlayIdle(selectedIndexRef.current, layouts, naturalInsetsRef.current, container);
    }
  }, []);

  // Write overlay to exact resting position with CSS transition.
  // Keeps displayCenterRef in sync so next interaction has no position jump.
  function writeOverlayIdle(
    index: number,
    layouts: TabLayout[],
    insets: { top: number; bottom: number },
    container: HTMLDivElement,
  ) {
    const overlay = overlayRef.current;
    const content = overlayContentRef.current;
    if (!overlay || !content || layouts.length === 0) return;

    const layout = layouts[Math.min(index, layouts.length - 1)];
    const ch     = container.offsetHeight;
    const pillW  = layout.width;
    const left   = layout.centerX - pillW / 2;

    overlay.style.width      = `${pillW}px`;
    overlay.style.height     = `${ch - insets.top - insets.bottom}px`;
    overlay.style.transform  = `translate3d(${left}px, ${insets.top}px, 0)`;
    overlay.style.opacity    = '1';
    overlay.style.transition = CSS_SPRING;
    content.style.transform  = `translate3d(${-left}px, ${-insets.top}px, 0)`;
    content.style.transition = CSS_SPRING;

    displayCenterRef.current    = layout.centerX;
    displayCenterVelRef.current = 0;
    desiredCenterRef.current    = layout.centerX;
  }

  // Write overlay from computed inset values (RAF frames — transition: none).
  function writeOverlayFrame(
    left: number, right: number, top: number, bottom: number,
    cw: number, ch: number,
  ) {
    const overlay = overlayRef.current;
    const content = overlayContentRef.current;
    if (!overlay || !content) return;
    const w = Math.max(0, cw - left - right);
    const h = Math.max(0, ch - top  - bottom);
    overlay.style.width      = `${w}px`;
    overlay.style.height     = `${h}px`;
    overlay.style.transform  = `translate3d(${left}px, ${top}px, 0)`;
    overlay.style.transition = 'none';
    overlay.style.opacity    = '1';
    content.style.transform  = `translate3d(${-left}px, ${-top}px, 0)`;
    content.style.transition = 'none';
  }

  useEffect(() => {
    recomputeLayouts();
    const ro = new ResizeObserver(recomputeLayouts);
    if (containerRef.current) ro.observe(containerRef.current);
    document.fonts.ready.then(recomputeLayouts);
    return () => ro.disconnect();
  }, [recomputeLayouts]);

  // External nav (back/forward, programmatic): CSS-transition to new tab.
  useEffect(() => {
    if (animPhaseRef.current.tag !== 'idle') return;
    const container = containerRef.current;
    const layouts   = tabLayoutsRef.current;
    if (!container || layouts.length === 0) return;
    writeOverlayIdle(selectedIndex, layouts, naturalInsetsRef.current, container);
  }, [selectedIndex]);

  // ===========================================================================
  // RAF tick — reads ONLY refs, writes ONLY overlay DOM style
  // ===========================================================================

  const scheduleRAF = useCallback(() => {
    if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
    lastRafTimestampRef.current = -1;

    const tick = (timestamp: number) => {
      const overlay   = overlayRef.current;
      const content   = overlayContentRef.current;
      const container = containerRef.current;
      const layouts   = tabLayoutsRef.current;
      const insets    = naturalInsetsRef.current;
      if (!overlay || !content || !container || layouts.length === 0) return;

      const cw           = container.offsetWidth;
      const ch           = container.offsetHeight;
      const pillNaturalH = ch - insets.top - insets.bottom;

      // -----------------------------------------------------------------------
      // Time-delta: how many 60fps-equivalent steps to simulate this frame.
      // On a 30fps device each frame is ~33ms → 2 steps → same wall-clock speed
      // as 60fps. Capped to prevent spiral-of-death after tab regains focus.
      // -----------------------------------------------------------------------
      const dt = lastRafTimestampRef.current < 0
        ? FIXED_STEP_MS
        : Math.min(timestamp - lastRafTimestampRef.current, FIXED_STEP_MS * MAX_STEPS_PER_FRAME);
      lastRafTimestampRef.current = timestamp;
      const numSteps = Math.max(1, Math.round(dt / FIXED_STEP_MS));

      // -----------------------------------------------------------------------
      // Advance springs numSteps times (physics only, no DOM writes).
      // -----------------------------------------------------------------------
      let wentIdle = false;
      let idleTargetIndex = 0;

      for (let s = 0; s < numSteps && !wentIdle; s++) {
        const phase = animPhaseRef.current;

        // PRESSED — scale rises, position fixed
        if (phase.tag === 'pressed') {
          scaleFRef.current += (1.0 - scaleFRef.current) * SCALE_RISE_RATE;
        }

        // DRAGGING — tracking spring + deformation + scale
        else if (phase.tag === 'dragging') {
          displayCenterVelRef.current =
            displayCenterVelRef.current * TRACKING_DAMPING +
            (desiredCenterRef.current - displayCenterRef.current) * TRACKING_STIFFNESS;
          displayCenterRef.current += displayCenterVelRef.current;

          const vel = displayCenterVelRef.current;
          deformVelRef.current =
            deformVelRef.current * DEFORM_DAMPING +
            (Math.abs(vel) * DEFORM_DRIVE - deformRef.current) * DEFORM_STIFFNESS;
          deformRef.current = Math.max(-DEFORM_MAX, Math.min(DEFORM_MAX,
            deformRef.current + deformVelRef.current));

          scaleFRef.current += (1.0 - scaleFRef.current) * SCALE_RISE_RATE;
        }

        // SNAPPING — center springs toward target
        else if (phase.tag === 'snapping') {
          const targetCenter = (layouts[phase.targetIndex] ?? layouts[0]).centerX;

          displayCenterVelRef.current =
            displayCenterVelRef.current * SNAP_DAMPING +
            (targetCenter - displayCenterRef.current) * SNAP_STIFFNESS;
          displayCenterRef.current += displayCenterVelRef.current;

          const speed = Math.abs(displayCenterVelRef.current);
          if (speed > peakSnapSpeedRef.current) peakSnapSpeedRef.current = speed;

          const fingerUp   = gestureRef.current === null;
          const nearTarget = Math.abs(displayCenterRef.current - targetCenter) < SNAP_NEAR_TARGET_PX;
          const peaking    = peakSnapSpeedRef.current > 0.3 && speed < peakSnapSpeedRef.current * SNAP_PEAK_VELOCITY_FALLOFF;
          if (!scalingDownRef.current && fingerUp && (nearTarget || peaking)) {
            scalingDownRef.current = true;
          }

          if (!scalingDownRef.current) {
            scaleFRef.current += (1.0 - scaleFRef.current) * SNAP_SCALE_RISE;
          } else {
            scaleFRef.current += (0.0 - scaleFRef.current) * SNAP_SCALE_FALL;
          }

          if (scalingDownRef.current && scaleFRef.current < SNAP_SCALE_EXIT_THRESHOLD) {
            animPhaseRef.current = { tag: 'idle' };
            wentIdle = true;
            idleTargetIndex = phase.targetIndex;
          }
        }

        // BOUNCE — scale pulses in place
        else if (phase.tag === 'bounce') {
          if (bounceRisingRef.current) {
            scaleFRef.current += (BOUNCE_PEAK - scaleFRef.current) * BOUNCE_RISE_RATE;
            if (scaleFRef.current >= BOUNCE_PEAK * 0.92) bounceRisingRef.current = false;
          } else {
            scaleFRef.current += (0.0 - scaleFRef.current) * BOUNCE_FALL_RATE;
          }

          if (!bounceRisingRef.current && scaleFRef.current < BOUNCE_EXIT_THRESHOLD) {
            animPhaseRef.current = { tag: 'idle' };
            wentIdle = true;
            idleTargetIndex = phase.tabIndex;
          }
        }
      }

      // -----------------------------------------------------------------------
      // Transition to idle (CSS spring takes over).
      // -----------------------------------------------------------------------
      if (wentIdle) {
        writeOverlayIdle(idleTargetIndex, layouts, insets, container);
        return;
      }

      // -----------------------------------------------------------------------
      // Write one frame from final spring state.
      // -----------------------------------------------------------------------
      const phase = animPhaseRef.current;

      if (phase.tag === 'pressed') {
        const sf      = scaleFRef.current;
        const centerX = displayCenterRef.current;
        const morphW  = getMorphedWidth(centerX, layouts);
        const expandW = morphW       * SCALE_WIDTH_FACTOR  * sf;
        const expandH = pillNaturalH * SCALE_HEIGHT_FACTOR * sf;
        writeOverlayFrame(
          centerX - morphW / 2 - expandW,
          cw - centerX - morphW / 2 - expandW,
          insets.top    - expandH,
          insets.bottom - expandH,
          cw, ch,
        );
      }

      else if (phase.tag === 'dragging') {
        const vel    = displayCenterVelRef.current;
        const dir    = vel >= 0 ? 1 : -1;
        const deform = deformRef.current;
        const sf     = scaleFRef.current;

        const morphW  = getMorphedWidth(displayCenterRef.current, layouts);
        const expandW = morphW       * SCALE_WIDTH_FACTOR  * sf;
        const expandH = pillNaturalH * SCALE_HEIGHT_FACTOR * sf;

        const hLead      = deform > 0 ? deform * expandW * LEAD_SHIFT_FRACTION       : 0;
        const hNarrow    = deform < 0 ? (-deform) * expandW * NARROW_SHRINK_FRACTION : 0;
        const leftExtra  = dir < 0 ? hLead : hNarrow;
        const rightExtra = dir > 0 ? hLead : hNarrow;
        const vSquash    = deform > 0 ?  deform * expandH * SQUASH_FRACTION  : 0;
        const vStretch   = deform < 0 ? -deform * expandH * STRETCH_FRACTION : 0;

        writeOverlayFrame(
          displayCenterRef.current - morphW / 2 - expandW - leftExtra,
          cw - displayCenterRef.current - morphW / 2 - expandW - rightExtra,
          insets.top    - expandH + vSquash - vStretch,
          insets.bottom - expandH + vSquash - vStretch,
          cw, ch,
        );
      }

      else if (phase.tag === 'snapping') {
        const vel     = displayCenterVelRef.current;
        const sf      = scaleFRef.current;
        const stretch = Math.min(Math.abs(vel) * 0.6, SNAP_MAX_STRETCH_PX);
        const squash  = (stretch / SNAP_MAX_STRETCH_PX) * SNAP_MAX_SQUASH_PX;
        const hLeft   = vel < 0 ? stretch : 0;
        const hRight  = vel > 0 ? stretch : 0;

        const morphW  = getMorphedWidth(displayCenterRef.current, layouts);
        const expandW = morphW       * SCALE_WIDTH_FACTOR  * sf;
        const expandH = pillNaturalH * SCALE_HEIGHT_FACTOR * sf;

        writeOverlayFrame(
          displayCenterRef.current - morphW / 2 - expandW - hLeft,
          cw - displayCenterRef.current - morphW / 2 - expandW - hRight,
          insets.top    - expandH + squash,
          insets.bottom - expandH + squash,
          cw, ch,
        );
      }

      else if (phase.tag === 'bounce') {
        const layout  = layouts[phase.tabIndex] ?? layouts[0];
        const sf      = scaleFRef.current;
        const morphW  = layout.width;
        const centerX = layout.centerX;
        const expandW = morphW       * SCALE_WIDTH_FACTOR  * sf;
        const expandH = pillNaturalH * SCALE_HEIGHT_FACTOR * sf;

        writeOverlayFrame(
          centerX - morphW / 2 - expandW,
          cw - centerX - morphW / 2 - expandW,
          insets.top    - expandH,
          insets.bottom - expandH,
          cw, ch,
        );
      }

      // idle — stop RAF
      if (phase.tag !== 'idle') {
        rafIdRef.current = requestAnimationFrame(tick);
      }
    };

    rafIdRef.current = requestAnimationFrame(tick);
  }, []);

  // ===========================================================================
  // Pointer handlers
  // setPointerCapture → all pointermove/up events come to this element.
  // gestureRef tracks the active finger independently of the animation phase.
  // ===========================================================================

  const handlePointerDown = useCallback((e: React.PointerEvent) => {
    if (!e.isPrimary) return;
    e.currentTarget.setPointerCapture(e.pointerId);

    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }

    const layouts   = tabLayoutsRef.current;
    const container = containerRef.current;
    if (!container || layouts.length === 0) return;

    const containerLeft     = container.getBoundingClientRect().left; // single read at press
    const pointerContainerX = e.clientX - containerLeft;
    const clickedIndex      = findNearestIndex(pointerContainerX, layouts);
    const wasAlreadySelected = clickedIndex === selectedIndexRef.current;

    // Reset velocity tracking
    pointerVelRef.current   = 0;
    lastPointerXRef.current = e.clientX;
    lastPointerTRef.current = performance.now();

    if (wasAlreadySelected) {
      // ---- Same tab: hold in place, scale up. Bounce on release. ----
      gestureRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        startPillX: displayCenterRef.current || layouts[clickedIndex].centerX,
        clickedIndex,
        wasAlreadySelected: true,
        hasDragged: false,
      };

      // Reset scale so it rises freshly
      scaleFRef.current    = 0;
      deformRef.current    = 0;
      deformVelRef.current = 0;
      animPhaseRef.current = { tag: 'pressed' };

    } else {
      // ---- Different tab: immediately start snapping toward it. ----
      // If the user lifts (tap): snap plays out naturally.
      // If the user drags before snap settles: interrupt → dragging.
      onSelect(clickedIndex);

      gestureRef.current = {
        pointerId: e.pointerId,
        startX: e.clientX,
        // Drag anchor = clicked tab center (pill will jump there if drag starts)
        startPillX: layouts[clickedIndex].centerX,
        clickedIndex,
        wasAlreadySelected: false,
        hasDragged: false,
      };

      // Seed the snap spring from current display position → clicked tab
      peakSnapSpeedRef.current = 0;
      scalingDownRef.current   = false;
      scaleFRef.current        = 0;
      deformRef.current        = 0;
      deformVelRef.current     = 0;
      // displayCenterRef + displayCenterVelRef keep whatever they had — no jump
      animPhaseRef.current = { tag: 'snapping', targetIndex: clickedIndex };
    }

    scheduleRAF();
  }, [onSelect, scheduleRAF]);

  const handlePointerMove = useCallback((e: React.PointerEvent) => {
    const gesture = gestureRef.current;
    if (!gesture || e.pointerId !== gesture.pointerId) return;

    // Update velocity EMA
    const now = performance.now();
    const dt  = now - lastPointerTRef.current;
    if (lastPointerTRef.current > 0 && dt > 0 && dt < VELOCITY_MAX_DT_MS) {
      const rawV = (e.clientX - lastPointerXRef.current) / dt;
      pointerVelRef.current = pointerVelRef.current * (1 - VELOCITY_EMA_WEIGHT) + rawV * VELOCITY_EMA_WEIGHT;
    }
    lastPointerXRef.current = e.clientX;
    lastPointerTRef.current = now;

    const dx = e.clientX - gesture.startX;

    if (!gesture.hasDragged) {
      if (Math.abs(dx) < DRAG_THRESHOLD_PX) return;

      // Crossed threshold → become a drag
      gesture.hasDragged = true;
      document.body.style.cursor     = 'grabbing';
      document.body.style.userSelect = 'none';

      // Jump pill to the drag anchor (clicked tab center).
      // For same-tab: no-op (already there). For non-selected: instant jump,
      // interrupting any in-progress snap.
      displayCenterRef.current    = gesture.startPillX;
      displayCenterVelRef.current = 0;
      deformRef.current    = 0;
      deformVelRef.current = 0;

      // Re-anchor startX to RIGHT NOW so the pill starts tracking from the
      // clicked tab center with zero offset. This eliminates the gap between
      // tap position and tab center.
      gesture.startX = e.clientX;

      // Switch animation to dragging
      animPhaseRef.current = { tag: 'dragging' };
      desiredCenterRef.current = gesture.startPillX; // zero delta at this moment
      return;
    }

    // Already dragging — update desired center
    const rawX = gesture.startPillX + (e.clientX - gesture.startX);
    desiredCenterRef.current = applyRubberBand(rawX, tabLayoutsRef.current);
  }, []);

  const handlePointerUp = useCallback((e: React.PointerEvent) => {
    const gesture = gestureRef.current;
    if (!gesture || e.pointerId !== gesture.pointerId) return;
    gestureRef.current = null;

    document.body.style.cursor     = '';
    document.body.style.userSelect = '';

    const layouts = tabLayoutsRef.current;

    if (gesture.hasDragged) {
      // Fling release → snap to nearest projected tab
      const projected   = projectWithVelocity(displayCenterRef.current, pointerVelRef.current);
      const targetIndex = findNearestIndex(projected, layouts);
      onSelect(targetIndex);

      peakSnapSpeedRef.current = 0;
      scalingDownRef.current   = false;
      // displayCenter + vel keep current state → seamless transition
      animPhaseRef.current = { tag: 'snapping', targetIndex };
      // RAF is already running, picks up new phase on next frame

    } else if (gesture.wasAlreadySelected) {
      // Pure tap on current tab → bounce
      // Continue from current scale so the lift doesn't reset and re-lift (jank).
      // If already near/past peak, start falling; otherwise keep rising.
      bounceRisingRef.current = scaleFRef.current < BOUNCE_PEAK * 0.92;
      animPhaseRef.current    = { tag: 'bounce', tabIndex: gesture.clickedIndex };
      scheduleRAF();

    } else {
      // Pure tap on non-selected tab → snap was already started on pointerDown.
      // Just let it finish. Nothing to do here.
    }
  }, [onSelect, scheduleRAF]);

  // Cleanup on unmount
  useEffect(() => () => {
    if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
    document.body.style.cursor     = '';
    document.body.style.userSelect = '';
  }, []);

  // ===========================================================================
  // JSX
  // ===========================================================================

  return (
    <div data-navbar className="fixed top-5 left-5 z-50">
      <div
        ref={containerRef}
        className="relative flex flex-row items-start p-[2px]"
        style={{ touchAction: 'none' }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        {/* Frosted glass background */}
        <div className="absolute inset-0 backdrop-blur-[6px] bg-white/70 rounded-full overflow-hidden shadow-[0_4px_24px_rgba(0,0,0,0.16)]" />

        {/* Base buttons — black text */}
        {tabs.map((tab, i) => (
          <button
            key={tab.page}
            ref={(el) => { tabButtonRefs.current[i] = el; }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(i); }
            }}
            className="flex items-center justify-center gap-[4px] px-[12px] py-[8px] text-[18px] font-['Inter'] text-black cursor-pointer relative bg-transparent border-0 rounded-full select-none z-10"
            data-nav-item={tab.page}
          >
            <tab.Icon size={16} />
            {tab.label}
          </button>
        ))}

        {/* Green pill overlay */}
        <div
          ref={overlayRef}
          className="absolute overflow-hidden rounded-full pointer-events-none z-20"
          style={{
            top: 0, left: 0, width: 0, height: 0, opacity: 0,
            background: '#166534',
            willChange: 'transform, width, height, opacity',
          }}
        >
          <div
            ref={overlayContentRef}
            className="absolute top-0 left-0 flex flex-row items-start p-[2px]"
            style={{ willChange: 'transform' }}
          >
            {tabs.map((tab) => (
              <div
                key={tab.page}
                className="flex items-center justify-center gap-[4px] px-[12px] py-[8px] text-[18px] font-['Inter'] text-white rounded-full select-none"
              >
                <tab.Icon size={16} />
                {tab.label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
