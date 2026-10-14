import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { TopBar } from './TopBar';

/**
 * Round 40 — the app name must cost ONE line.
 *
 * On a 360px phone "SRM FieldInspect UAT" wrapped onto three lines inside a
 * 56px-tall bar, pushing the whole page down and overlapping the controls. The
 * cause was two missing classes rather than anything structural: the name had
 * no `truncate`, and its flex parent had no `min-w-0`, so the row refused to
 * let the name shrink below its content width.
 *
 * Asserted on the classes because that IS the fix — jsdom has no layout, so
 * there is no rendered height to measure here.
 */
function renderTopBar() {
  return render(
    <MemoryRouter>
      <TopBar />
    </MemoryRouter>,
  );
}

describe('TopBar — the app name on a narrow screen', () => {
  it('truncates instead of wrapping', () => {
    const { container } = renderTopBar();
    const name = container.querySelector('span.truncate');

    expect(name).not.toBeNull();
    expect(name!.className).toContain('truncate');
  });

  it('lets its row shrink, so the name can actually be truncated', () => {
    // Without min-w-0 on the parent, `truncate` never takes effect: a flex
    // item defaults to min-width:auto and simply overflows instead.
    const { container } = renderTopBar();
    const name = container.querySelector('span.truncate');

    expect(name!.parentElement!.className).toContain('min-w-0');
  });

  it('starts one step smaller on phones and grows from sm up', () => {
    const { container } = renderTopBar();
    const name = container.querySelector('span.truncate')!;

    expect(name.className).toContain('text-base');
    expect(name.className).toContain('sm:text-lg');
  });

  it('still shows a name', () => {
    // Not pinned to a literal: APP_NAME comes from VITE_APP_NAME, which
    // differs per environment (UAT ships "SRM FieldInspect UAT"). What this
    // round must not do is lose the name while making it fit.
    const { container } = renderTopBar();
    const name = container.querySelector('span.truncate')!;

    expect(name.textContent?.trim().length).toBeGreaterThan(0);
  });
});
