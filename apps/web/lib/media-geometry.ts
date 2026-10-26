export type MediaRect = { left: number; top: number; width: number; height: number };

export function containedMediaRect(width: number, height: number, mediaWidth: number, mediaHeight: number): MediaRect | null {
  if ([width, height, mediaWidth, mediaHeight].some((value) => !Number.isFinite(value) || value < 0)) {
    throw new RangeError("Media dimensions must be finite and non-negative.");
  }
  if (!width || !height || !mediaWidth || !mediaHeight) return null;
  const scale = Math.min(width / mediaWidth, height / mediaHeight);
  const contentWidth = mediaWidth * scale, contentHeight = mediaHeight * scale;
  return { left: (width - contentWidth) / 2, top: (height - contentHeight) / 2, width: contentWidth, height: contentHeight };
}
