export function inspectDarkContrast() {
  function channels(value: string) {
    const numbers = value.match(/[\d.]+/g);
    if (!numbers || numbers.length < 3)
      throw new Error("Unsupported computed color: " + value);
    return numbers.map(Number);
  }
  function luminance(values: number[]) {
    const linear = values.slice(0, 3).map((value) => {
      const component = value / 255;
      return component <= 0.04045
        ? component / 12.92
        : ((component + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * linear[0]! + 0.7152 * linear[1]! + 0.0722 * linear[2]!;
  }
  const results: { target: string; ratio: number }[] = [];
  for (const selector of [
    "body",
    ".sample-status",
    ".pill",
    ".recorded-choice p",
    "button:not(:disabled)",
  ]) {
    for (const target of document.querySelectorAll<HTMLElement>(selector)) {
      if (
        !target.getClientRects().length ||
        getComputedStyle(target).visibility === "hidden"
      )
        continue;
      let backdrop: HTMLElement | null = target;
      let background: number[] | null = null;
      while (backdrop) {
        const color = channels(getComputedStyle(backdrop).backgroundColor);
        if (color.length === 3 || color[3] === 1) {
          background = color;
          break;
        }
        if (color[3] !== 0)
          throw new Error("Translucent background needs explicit compositing");
        backdrop = backdrop.parentElement;
      }
      if (background === null)
        throw new Error("No opaque background for " + selector);
      const foreground = luminance(channels(getComputedStyle(target).color));
      const back = luminance(background);
      results.push({
        target: selector,
        ratio:
          (Math.max(foreground, back) + 0.05) /
          (Math.min(foreground, back) + 0.05),
      });
    }
  }
  return {
    dark: getComputedStyle(document.documentElement).colorScheme === "dark",
    background_luminance: luminance(
      channels(getComputedStyle(document.body).backgroundColor),
    ),
    results,
  };
}
