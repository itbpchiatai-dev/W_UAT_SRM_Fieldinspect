import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { CompactScores } from './CompactScores';

describe('CompactScores', () => {
  it('renders the 4 scores as bare numbers, with no criterion labels', () => {
    render(<CompactScores scores={[8, 7, 9, 6]} />);
    const group = screen.getByLabelText('คะแนนการตรวจ 4 หัวข้อ');
    expect(within(group).getByText('8')).toBeTruthy();
    expect(within(group).getByText('7')).toBeTruthy();
    expect(within(group).getByText('9')).toBeTruthy();
    expect(within(group).getByText('6')).toBeTruthy();
    // No hardcoded criterion words leak in.
    expect(screen.queryByText(/เตรียมแปลง|ดูแลรักษา|ต้านทาน/)).toBeNull();
  });

  it('renders — for a null score', () => {
    render(<CompactScores scores={[null, 5, null, null]} />);
    const group = screen.getByLabelText('คะแนนการตรวจ 4 หัวข้อ');
    expect(within(group).getAllByText('—')).toHaveLength(3);
    expect(within(group).getByText('5')).toBeTruthy();
  });

  it('flags a low score (<= 3) with the orange tone', () => {
    render(<CompactScores scores={[3, 8, 9, 6]} />);
    expect(screen.getByText('3').className).toContain('text-orange-700');
    expect(screen.getByText('8').className).not.toContain('text-orange-700');
  });
});
