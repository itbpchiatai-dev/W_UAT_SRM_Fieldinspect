/**
 * ScoreButtons — the tap-first 1–10 score picker that replaced the range
 * sliders on both inspection forms: 10 number chips + an explicit "ว่าง"
 * chip that clears back to null.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ScoreButtons } from './ScoreButtons';

describe('ScoreButtons', () => {
  it('renders buttons 1..10 plus the ว่าง chip (11 total)', () => {
    render(<ScoreButtons label="การเตรียมแปลง" value={null} onChange={() => {}} />);

    const group = screen.getByRole('group', { name: 'การเตรียมแปลง' });
    const buttons = group.querySelectorAll('button');
    expect(buttons).toHaveLength(11);
    for (let n = 1; n <= 10; n++) {
      expect(screen.getByRole('button', { name: String(n) })).toBeTruthy();
    }
    expect(screen.getByRole('button', { name: 'ว่าง' })).toBeTruthy();
  });

  it('tapping a number reports that score', () => {
    const onChange = vi.fn();
    render(<ScoreButtons label="สภาพอากาศ" value={null} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: '7' }));
    expect(onChange).toHaveBeenCalledWith(7);
  });

  it('shows the selected score as pressed with the "n / 10" summary', () => {
    render(<ScoreButtons label="การดูแลรักษา" value={7} onChange={() => {}} />);

    expect(screen.getByRole('button', { name: '7', pressed: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ว่าง', pressed: false })).toBeTruthy();
    expect(screen.getByText('7 / 10')).toBeTruthy();
  });

  it('tapping ว่าง clears to null, and ว่าง reads as pressed while empty', () => {
    const onChange = vi.fn();
    render(<ScoreButtons label="การเตรียมแปลง" value={7} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'ว่าง' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('empty state marks ว่าง pressed and shows ยังไม่ให้คะแนน', () => {
    render(<ScoreButtons label="ความต้านทานของสายพันธุ์" value={null} onChange={() => {}} />);

    expect(screen.getByRole('button', { name: 'ว่าง', pressed: true })).toBeTruthy();
    expect(screen.getByText('ยังไม่ให้คะแนน')).toBeTruthy();
  });

  it('tapping the already-selected number clears it (OptionButtons convention)', () => {
    const onChange = vi.fn();
    render(<ScoreButtons label="สภาพอากาศ" value={5} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: '5' }));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('disabled disables every chip', () => {
    render(<ScoreButtons label="การเตรียมแปลง" value={3} onChange={() => {}} disabled />);

    const group = screen.getByRole('group', { name: 'การเตรียมแปลง' });
    group.querySelectorAll('button').forEach((b) => expect(b.disabled).toBe(true));
  });
});
