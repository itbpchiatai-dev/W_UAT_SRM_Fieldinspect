/**
 * Menus — tree editor.
 *
 * Reorder strategy is intentionally simple: up/down arrows swap the
 * order_index field with the adjacent SIBLING. Drag-and-drop is a Phase D
 * task and would pull in dnd-kit (a 25KB+ extra dep) — out of scope here.
 *
 * The tree is flat-rendered with indentation. The full subtree below a
 * node is implicit (children render under the parent in the DOM order).
 */
import { useMemo, useState } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronUp, Loader2, Pencil, Plus, Trash2 } from 'lucide-react';
import * as LucideIcons from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import {
  createMenu,
  deleteMenu,
  listMenus,
  swapMenuOrder,
  updateMenu,
  type MenuCreatePayload,
} from '../../api/menus';
import { listPermissions } from '../../api/permissions';
import type { MenuItemTree } from '../../types/auth';

const menuSchema = z.object({
  key: z.string().min(1, 'common.required').regex(/^[a-z][a-z0-9_]*$/, 'settings.menus.keyFormat'),
  labelTh: z.string().min(1, 'common.required'),
  labelEn: z.string().min(1, 'common.required'),
  icon: z.string().optional().default(''),
  path: z.string().min(1, 'common.required'),
  parentId: z.string().optional().default(''),
  requiredPermissionKey: z.string().min(1, 'common.required'),
  orderIndex: z.coerce.number().int().default(0),
});
type MenuFormValues = z.infer<typeof menuSchema>;

function flatten(nodes: MenuItemTree[], depth = 0): { node: MenuItemTree; depth: number }[] {
  const out: { node: MenuItemTree; depth: number }[] = [];
  for (const n of nodes) {
    out.push({ node: n, depth });
    if (n.children?.length) out.push(...flatten(n.children, depth + 1));
  }
  return out;
}

function resolveIcon(name: string | null | undefined): LucideIcon | null {
  if (!name) return null;
  const lib = LucideIcons as unknown as Record<string, LucideIcon>;
  // Accept both the documented PascalCase ("LayoutDashboard") and the
  // kebab-case form shown on lucide.dev ("layout-dashboard") — normalise
  // to PascalCase so either spelling in the seed / admin form resolves.
  const pascal = name
    .split(/[-_]/)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');
  return lib[name] ?? lib[pascal] ?? null;
}

export function Menus() {
  const { t, i18n } = useTranslation();
  const lang = i18n.language;
  const qc = useQueryClient();
  const [editing, setEditing] = useState<MenuItemTree | null>(null);
  const [creating, setCreating] = useState(false);

  const { data: tree = [], isLoading } = useQuery({
    queryKey: ['menus'],
    queryFn: listMenus,
  });
  const flat = useMemo(() => flatten(tree), [tree]);

  const deleteM = useMutation({
    mutationFn: (id: string) => deleteMenu(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['menus'] }),
  });

  const swapM = useMutation({
    mutationFn: ({ a, b }: { a: { id: string; orderIndex: number }; b: { id: string; orderIndex: number } }) =>
      swapMenuOrder(a, b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['menus'] }),
  });

  const siblingsOf = (node: MenuItemTree): MenuItemTree[] => {
    const findIn = (nodes: MenuItemTree[]): MenuItemTree[] | null => {
      if (nodes.some((n) => n.id === node.id)) return nodes;
      for (const n of nodes) {
        const found = findIn(n.children ?? []);
        if (found) return found;
      }
      return null;
    };
    return findIn(tree) ?? [];
  };

  const move = (node: MenuItemTree, dir: -1 | 1) => {
    const siblings = siblingsOf(node).slice().sort((a, b) => a.orderIndex - b.orderIndex);
    const idx = siblings.findIndex((n) => n.id === node.id);
    const target = siblings[idx + dir];
    if (!target) return;
    swapM.mutate({
      a: { id: node.id, orderIndex: node.orderIndex },
      b: { id: target.id, orderIndex: target.orderIndex },
    });
  };

  return (
    <div className="container mx-auto px-4 py-8 sm:px-6 lg:px-8">
      <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-xl font-bold">{t('settings.menus.title')}</h1>
          <p className="mt-1 text-sm text-muted-foreground">{t('settings.menus.description')}</p>
        </div>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90"
        >
          <Plus className="h-4 w-4" />
          {t('settings.menus.new')}
        </button>
      </header>

      <section className="mt-6 overflow-x-auto rounded-lg border border-border bg-card shadow-sm">
        {isLoading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <ul className="divide-y divide-border">
            {flat.map(({ node, depth }) => {
              const Icon = resolveIcon(node.icon);
              return (
                <li key={node.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                  <span style={{ paddingLeft: `${depth * 16}px` }} className="flex flex-1 items-center gap-2">
                    {Icon ? <Icon className="h-4 w-4 text-muted-foreground" /> : <span className="h-4 w-4" />}
                    <span className="text-foreground" title={`${node.key}  /  ${lang === 'en' ? node.labelTh : node.labelEn}`}>
                      {lang === 'en' ? node.labelEn : node.labelTh}
                    </span>
                    {node.path ? (
                      <span className="ml-2 text-xs text-muted-foreground">{node.path}</span>
                    ) : null}
                  </span>
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => move(node, -1)}
                      className="rounded-md p-1.5 hover:bg-secondary"
                      aria-label={t('settings.menus.moveUp')}
                    >
                      <ChevronUp className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => move(node, 1)}
                      className="rounded-md p-1.5 hover:bg-secondary"
                      aria-label={t('settings.menus.moveDown')}
                    >
                      <ChevronDown className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(node)}
                      className="rounded-md p-1.5 hover:bg-secondary"
                      aria-label={t('common.edit')}
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (confirm(t('settings.menus.confirmDelete', { key: node.key }))) {
                          deleteM.mutate(node.id);
                        }
                      }}
                      className="rounded-md p-1.5 text-destructive hover:bg-destructive/10"
                      aria-label={t('common.delete')}
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </li>
              );
            })}
            {flat.length === 0 ? (
              <li className="px-4 py-6 text-center text-sm text-muted-foreground">
                {t('common.noResults')}
              </li>
            ) : null}
          </ul>
        )}
      </section>

      {creating || editing ? (
        <MenuEditor
          existing={editing}
          flat={flat}
          onClose={() => {
            setEditing(null);
            setCreating(false);
            qc.invalidateQueries({ queryKey: ['menus'] });
          }}
        />
      ) : null}
    </div>
  );
}

interface MenuEditorProps {
  existing: MenuItemTree | null;
  flat: { node: MenuItemTree; depth: number }[];
  onClose: () => void;
}

function MenuEditor({ existing, flat, onClose }: MenuEditorProps) {
  const { t } = useTranslation();
  const qc = useQueryClient();

  const { data: perms = [] } = useQuery({
    queryKey: ['permissions'],
    queryFn: listPermissions,
    staleTime: 5 * 60 * 1000,
  });

  const defaults: MenuFormValues = {
    key: existing?.key ?? '',
    labelTh: existing?.labelTh ?? '',
    labelEn: existing?.labelEn ?? '',
    icon: existing?.icon ?? '',
    path: existing?.path ?? '',
    parentId: existing?.parentId ?? '',
    requiredPermissionKey: existing?.requiredPermissionKey ?? '',
    orderIndex: existing?.orderIndex ?? 0,
  };

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<MenuFormValues>({
    resolver: zodResolver(menuSchema),
    defaultValues: defaults,
  });

  const iconName = watch('icon');
  const IconPreview = resolveIcon(iconName);

  const onSubmit = async (values: MenuFormValues) => {
    const payload: MenuCreatePayload = {
      key: values.key,
      labelTh: values.labelTh,
      labelEn: values.labelEn,
      icon: values.icon || null,
      path: values.path,
      parentId: values.parentId || null,
      orderIndex: values.orderIndex,
      requiredPermissionKey: values.requiredPermissionKey,
    };
    if (existing) {
      await updateMenu(existing.id, payload);
    } else {
      await createMenu(payload);
    }
    qc.invalidateQueries({ queryKey: ['menus'] });
    onClose();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-40 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] w-full max-w-xl flex-col overflow-y-auto rounded-lg border border-border bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold">
          {t(existing ? 'settings.menus.edit' : 'settings.menus.new')}
        </h2>
        <form onSubmit={handleSubmit(onSubmit)} className="flex flex-col gap-3">
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.key')}</span>
            <input
              type="text"
              readOnly={!!existing}
              {...register('key')}
              className="rounded-md border border-input bg-background px-3 py-2 text-sm font-mono read-only:opacity-70 focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {errors.key ? (
              <span className="text-xs text-destructive">{t(errors.key.message ?? '')}</span>
            ) : null}
          </label>

          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">{t('settings.menus.fields.labelTh')}</span>
              <input type="text" {...register('labelTh')} className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">{t('settings.menus.fields.labelEn')}</span>
              <input type="text" {...register('labelEn')} className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
            </label>
          </div>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.path')}</span>
            <input type="text" {...register('path')} className="rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring" />
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.icon')}</span>
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="LayoutDashboard"
                {...register('icon')}
                className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <span className="flex h-9 w-9 items-center justify-center rounded-md border border-border bg-background">
                {IconPreview ? <IconPreview className="h-4 w-4" /> : <span className="text-xs text-muted-foreground">?</span>}
              </span>
            </div>
            <span className="text-xs text-muted-foreground">{t('settings.menus.iconHelp')}</span>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.parent')}</span>
            <select {...register('parentId')} className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring">
              <option value="">{t('settings.menus.noParent')}</option>
              {flat
                .filter(({ node }) => !existing || node.id !== existing.id)
                .map(({ node, depth }) => (
                  <option key={node.id} value={node.id}>
                    {'  '.repeat(depth)}{node.labelTh} ({node.key})
                  </option>
                ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.permission')}</span>
            <select {...register('requiredPermissionKey')} className="rounded-md border border-input bg-background px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-ring">
              <option value="">—</option>
              {perms.filter((p) => p.isMenu).map((p) => (
                <option key={p.id} value={p.key}>{p.key}</option>
              ))}
            </select>
            {errors.requiredPermissionKey ? (
              <span className="text-xs text-destructive">{t(errors.requiredPermissionKey.message ?? '')}</span>
            ) : null}
          </label>

          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">{t('settings.menus.fields.order')}</span>
            <input type="number" {...register('orderIndex')} className="rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring" />
          </label>

          <div className="mt-2 flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-border bg-card px-4 py-2 text-sm text-foreground hover:bg-secondary">
              {t('common.cancel')}
            </button>
            <button type="submit" disabled={isSubmitting} className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60">
              {isSubmitting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              {t('common.save')}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default Menus;
