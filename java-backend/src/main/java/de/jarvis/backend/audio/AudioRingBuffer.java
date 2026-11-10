package de.jarvis.backend.audio;

import java.util.concurrent.atomic.AtomicReferenceArray;

/**
 * A small bounded FIFO for generated audio frames.
 *
 * <p>The room owns the generation number.  Changing it through {@link #setEpoch(long)}
 * clears the queue before the new generation can be offered; the buffer never invents
 * a second, independent generation.</p>
 */
public final class AudioRingBuffer {
    private final AtomicReferenceArray<AudioFrame> slots;
    private long epoch;
    private long write;
    private long read;
    private int size;

    public AudioRingBuffer(int capacity) {
        if (capacity < 2) throw new IllegalArgumentException("capacity must be at least 2");
        this.slots = new AtomicReferenceArray<>(capacity);
    }

    public synchronized long epoch() {
        return epoch;
    }

    /** Clear all pending frames and make {@code nextEpoch} the only accepted generation. */
    public synchronized long setEpoch(long nextEpoch) {
        if (nextEpoch < 0) throw new IllegalArgumentException("epoch");
        for (int i = 0; i < slots.length(); i++) slots.set(i, null);
        epoch = nextEpoch;
        write = 0;
        read = 0;
        size = 0;
        return epoch;
    }

    /** Remove pending frames without changing the room generation. */
    public synchronized void clearBuffer() {
        for (int i = 0; i < slots.length(); i++) slots.set(i, null);
        write = 0;
        read = 0;
        size = 0;
    }

    /** Compatibility helper for callers that only need a fresh generation. */
    public synchronized long purge() {
        return setEpoch(epoch + 1);
    }

    /** Clear the queue and use the supplied generation. */
    public synchronized long purge(long nextEpoch) {
        return setEpoch(nextEpoch);
    }

    public synchronized boolean offer(AudioFrame frame) {
        if (frame == null || frame.epoch() != epoch || size == slots.length()) return false;
        slots.set((int) (write % slots.length()), frame);
        write++;
        size++;
        return true;
    }

    public synchronized AudioFrame poll() {
        if (size == 0) return null;
        AudioFrame frame = slots.getAndSet((int) (read % slots.length()), null);
        read++;
        size--;
        return frame;
    }

    public synchronized int size() {
        return size;
    }

    public int capacity() {
        return slots.length();
    }
}
