'use client';

import { useEffect, useState } from 'react';

interface IconWrapperProps {
  src: string;
  alt?: string;
  className?: string;
  color?: string;
}

export function IconWrapper({ src, alt = '', className = '', color }: IconWrapperProps) {
  const [svgContent, setSvgContent] = useState<string>('');

  useEffect(() => {
    fetch(src)
      .then(res => res.text())
      .then(svg => {
        let modifiedSvg = svg;

        // If color is provided, replace fill/stroke attributes
        if (color) {
          const colorValue = color.startsWith('#') ? color : `#${color}`;
          // Replace fill attributes (but not fill="none")
          modifiedSvg = modifiedSvg.replace(/fill="(?!none)[^"]*"/g, `fill="${colorValue}"`);
          // Also replace stroke attributes if present
          modifiedSvg = modifiedSvg.replace(/stroke="(?!none)[^"]*"/g, `stroke="${colorValue}"`);
        }

        // Inject className into the SVG element
        if (className) {
          // Check if SVG already has a class attribute
          if (modifiedSvg.includes('class="')) {
            modifiedSvg = modifiedSvg.replace(/class="([^"]*)"/, `class="$1 ${className}"`);
          } else {
            // Add class attribute after the opening <svg tag
            modifiedSvg = modifiedSvg.replace(/<svg/, `<svg class="${className}"`);
          }
        }

        setSvgContent(modifiedSvg);
      })
      .catch(err => console.error('Error loading SVG:', err));
  }, [src, color, className]);

  if (!svgContent) {
    return <div className={className} aria-label={alt} />;
  }

  return (
    <div
      dangerouslySetInnerHTML={{ __html: svgContent }}
      aria-label={alt}
      role="img"
    />
  );
}
