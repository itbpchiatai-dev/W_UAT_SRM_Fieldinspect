/**
 * OptionButtons — the tap-chip picker behind MasterDataButtons /
 * PublicMasterDataButtons. Focus here is the multi-select mode added for
 * สภาพอากาศ: `value` holds a ", "-joined list, chips toggle independently.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OptionButtons, splitMultiValue } from './OptionButtons';

const WEATHER = ['แจ่มใส', 'มีเมฆ', 'ฝนตก'];

describe('splitMultiValue', () => {
  it('splits a joined value and ignores blanks', () => {
    expect(splitMultiValue('แจ่มใส, ฝนตก')).toEqual(['แจ่มใส', 'ฝนตก']);
    expect(splitMultiValue(null)).toEqual([]);
    expect(splitMultiValue('  ')).toEqual([]);
  });
});

describe('OptionButtons — single select (default, unchanged)', () => {
  it('selects on tap and clears on tapping the selected chip', () => {
    const onChange = vi.fn();
    const { rerender } = render(<OptionButtons options={WEATHER} value={null} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'แจ่มใส' }));
    expect(onChange).toHaveBeenLastCalledWith('แจ่มใส');

    rerender(<OptionButtons options={WEATHER} value="แจ่มใส" onChange={onChange} />);
    fireEvent.click(screen.getByRole('button', { name: 'แจ่มใส' }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('picking another option replaces the selection (not joins)', () => {
    const onChange = vi.fn();
    render(<OptionButtons options={WEATHER} value="แจ่มใส" onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: 'ฝนตก' }));
    expect(onChange).toHaveBeenLastCalledWith('ฝนตก');
  });
});

describe('OptionButtons — multiple', () => {
  it('adds a second selection as a ", "-joined value', () => {
    const onChange = vi.fn();
    render(<OptionButtons options={WEATHER} value="แจ่มใส" onChange={onChange} multiple />);

    fireEvent.click(screen.getByRole('button', { name: 'ฝนตก' }));
    expect(onChange).toHaveBeenLastCalledWith('แจ่มใส, ฝนตก');
  });

  it('marks every selected chip pressed', () => {
    render(<OptionButtons options={WEATHER} value="แจ่มใส, ฝนตก" onChange={() => {}} multiple />);

    expect(screen.getByRole('button', { name: 'แจ่มใส', pressed: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'ฝนตก', pressed: true })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'มีเมฆ', pressed: false })).toBeTruthy();
  });

  it('tapping a selected chip removes only that value', () => {
    const onChange = vi.fn();
    render(<OptionButtons options={WEATHER} value="แจ่มใส, ฝนตก" onChange={onChange} multiple />);

    fireEvent.click(screen.getByRole('button', { name: 'แจ่มใส' }));
    expect(onChange).toHaveBeenLastCalledWith('ฝนตก');
  });

  it('removing the last selected value reports null (not an empty string)', () => {
    const onChange = vi.fn();
    render(<OptionButtons options={WEATHER} value="ฝนตก" onChange={onChange} multiple />);

    fireEvent.click(screen.getByRole('button', { name: 'ฝนตก' }));
    expect(onChange).toHaveBeenLastCalledWith(null);
  });

  it('keeps a previously-saved value that is no longer in the options visible and removable', () => {
    const onChange = vi.fn();
    render(<OptionButtons options={WEATHER} value="พายุ, ฝนตก" onChange={onChange} multiple />);

    const stale = screen.getByRole('button', { name: 'พายุ', pressed: true });
    fireEvent.click(stale);
    expect(onChange).toHaveBeenLastCalledWith('ฝนตก');
  });
});
