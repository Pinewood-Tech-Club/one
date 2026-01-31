'use client';

import { useEffect, useState, useRef, useCallback } from 'react';

export function Carosuel({ children, className }: { children: React.ReactNode, className?: string }) {
    const scrollRef = useRef<HTMLDivElement>(null);
    const [showLeftGradient, setShowLeftGradient] = useState(false);
    const [showRightGradient, setShowRightGradient] = useState(false);

    const updateGradients = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;

        const { scrollLeft, scrollWidth, clientWidth } = el;
        const canScroll = scrollWidth > clientWidth;

        setShowLeftGradient(canScroll && scrollLeft > 0);
        setShowRightGradient(canScroll && scrollLeft < scrollWidth - clientWidth - 1);
    }, []);

    useEffect(() => {
        const el = scrollRef.current;
        if (!el) return;

        updateGradients();
        el.addEventListener('scroll', updateGradients);
        window.addEventListener('resize', updateGradients);

        return () => {
            el.removeEventListener('scroll', updateGradients);
            window.removeEventListener('resize', updateGradients);
        };
    }, [updateGradients]);

    return (
        <div className="relative">
            <div
                className="absolute left-0 top-0 h-full w-16 bg-gradient-to-r from-white dark:from-zinc-900 to-transparent z-10 pointer-events-none transition-opacity duration-200"
                style={{ opacity: showLeftGradient ? 1 : 0 }}
            />
            <div
                className="absolute right-0 top-0 h-full w-16 bg-gradient-to-l from-white dark:from-zinc-900 to-transparent z-10 pointer-events-none transition-opacity duration-200"
                style={{ opacity: showRightGradient ? 1 : 0 }}
            />
            <div ref={scrollRef} className={className}>
                {children}
            </div>
        </div>
    );
}

export function CarosuelItem({ children, className }: { children: React.ReactNode, className?: string }) {
    return (
        <div className={className}>
            {children}
        </div>
    );
}