"use client";

import { useEffect, useRef } from "react";

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius: number;
  color: string;
};

const ORB_TRANSFORMS = [
  (x: number, y: number) => `translate(${x}px, ${y}px)`,
  (x: number, y: number) => `translate(${-x * 1.5}px, ${-y * 1.5}px)`,
  (x: number, y: number) => `translate(${x * 0.5}px, ${-y * 0.8}px)`,
  (x: number, y: number) => `translate(${-x * 0.8}px, ${y * 1.2}px)`,
];

export default function LoginInteractiveBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const orbRefs = useRef<Array<HTMLDivElement | null>>([]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    let width = 0;
    let height = 0;
    let animationFrame = 0;
    let particles: Particle[] = [];
    const mouse: { x: number | null; y: number | null; radius: number } = {
      x: null,
      y: null,
      radius: 180,
    };

    const init = () => {
      const ratio = window.devicePixelRatio || 1;
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = width * ratio;
      canvas.height = height * ratio;
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

      const particleCount = reducedMotion
        ? 0
        : Math.min(96, Math.max(36, Math.floor((width * height) / 16000)));

      particles = Array.from({ length: particleCount }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.36,
        vy: (Math.random() - 0.5) * 0.36,
        radius: Math.random() * 1.8 + 0.8,
        color:
          Math.random() > 0.6
            ? "rgba(87, 73, 244, 0.36)"
            : "rgba(15, 95, 254, 0.26)",
      }));
    };

    const resetOrbs = () => {
      orbRefs.current.forEach((orb) => {
        if (orb) orb.style.transform = "translate(0px, 0px)";
      });
    };

    const handleMouseMove = (event: MouseEvent) => {
      mouse.x = event.clientX;
      mouse.y = event.clientY;

      if (reducedMotion) return;

      const moveX = (event.clientX - width / 2) * 0.04;
      const moveY = (event.clientY - height / 2) * 0.04;

      orbRefs.current.forEach((orb, index) => {
        if (orb) orb.style.transform = ORB_TRANSFORMS[index](moveX, moveY);
      });
    };

    const handleMouseOut = () => {
      mouse.x = null;
      mouse.y = null;
      resetOrbs();
    };

    const draw = () => {
      ctx.clearRect(0, 0, width, height);

      for (let i = 0; i < particles.length; i += 1) {
        const particle = particles[i];
        particle.x += particle.vx;
        particle.y += particle.vy;

        if (particle.x < 0 || particle.x > width) particle.vx *= -1;
        if (particle.y < 0 || particle.y > height) particle.vy *= -1;

        if (mouse.x !== null && mouse.y !== null) {
          const dx = mouse.x - particle.x;
          const dy = mouse.y - particle.y;
          const distance = Math.hypot(dx, dy);

          if (distance > 0 && distance < mouse.radius) {
            const force = (mouse.radius - distance) / mouse.radius;
            particle.x -= (dx / distance) * force * 3.5;
            particle.y -= (dy / distance) * force * 3.5;
          }
        }

        ctx.beginPath();
        ctx.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
        ctx.fillStyle = particle.color;
        ctx.fill();

        for (let j = i + 1; j < particles.length; j += 1) {
          const next = particles[j];
          const distance = Math.hypot(particle.x - next.x, particle.y - next.y);

          if (distance < 130) {
            ctx.beginPath();
            ctx.strokeStyle = `rgba(87, 73, 244, ${0.11 - distance / 1200})`;
            ctx.lineWidth = 0.8;
            ctx.moveTo(particle.x, particle.y);
            ctx.lineTo(next.x, next.y);
            ctx.stroke();
          }
        }
      }

      animationFrame = window.requestAnimationFrame(draw);
    };

    init();
    if (!reducedMotion) draw();

    window.addEventListener("resize", init);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseout", handleMouseOut);

    return () => {
      window.cancelAnimationFrame(animationFrame);
      window.removeEventListener("resize", init);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseout", handleMouseOut);
    };
  }, []);

  return (
    <div className="login-interactive-bg" aria-hidden="true">
      {["orb-1", "orb-2", "orb-3", "orb-4"].map((orbClass, index) => (
        <div
          key={orbClass}
          ref={(node) => {
            orbRefs.current[index] = node;
          }}
          className="login-orb-container"
        >
          <div className={`login-gradient-orb ${orbClass}`} />
        </div>
      ))}
      <canvas ref={canvasRef} className="login-particle-canvas" />
    </div>
  );
}
