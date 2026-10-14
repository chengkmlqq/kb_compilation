"use client";

/**
 * 模型选择下拉（assistant-ui 官方形态，对齐官方 examples 的 model-picker 风格：
 * round-full 胶囊按钮 + Base UI Menu，仅以 tailwind utility 实现）。
 *
 * 0.15 的 useExternalStoreRuntime 无 models trait（ModelPicker 是更高版本 v2
 * runtime 的能力），故此处自绘 + context 注入（page 层加载模型列表），
 * 挂在官方 thread.aui 的 composer 输入框左侧（ComposerAddAttachment 旁）。
 * context 无数据（其他页面/未加载完成）时渲染 null，不影响官方行为。
 */
import { createContext, useContext } from "react";
import { Menu } from "@base-ui/react/menu";
import { ChevronDownIcon } from "lucide-react";
import type { ModelItem } from "@/lib/api";
import { cn } from "@/lib/utils";

export interface ModelSelectorState {
  models: ModelItem[];
  value: string;
  onChange: (id: string) => void;
}

export const ModelSelectorContext = createContext<ModelSelectorState | null>(null);

export function ModelSelector() {
  const ctx = useContext(ModelSelectorContext);
  if (!ctx || ctx.models.length === 0) return null;
  const current = ctx.models.find((m) => m.id === ctx.value);

  return (
    <Menu.Root>
      <Menu.Trigger
        className="inline-flex items-center gap-1 rounded-full border border-foreground/10 bg-foreground/5 px-2 py-0.5 text-xs font-medium text-foreground transition-colors hover:bg-foreground/10 data-[popup-open]:bg-foreground/10"
        aria-label="选择模型"
      >
        {current?.display_name || current?.name || "默认模型"}
        <ChevronDownIcon className="size-3" />
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner sideOffset={8} align="start">
          <Menu.Popup className="bg-popover text-popover-foreground z-50 min-w-40 rounded-md border p-1 shadow-md outline-none">
            {ctx.models.map((m) => (
              <Menu.Item
                key={m.id}
                onClick={() => ctx.onChange(m.id)}
                className={cn(
                  "flex cursor-pointer items-center justify-between rounded-sm px-2 py-1.5 text-sm outline-none select-none",
                  "data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground",
                  m.id === ctx.value && "font-medium",
                )}
              >
                <span className="truncate">{m.display_name || m.name}</span>
                {m.id === ctx.value && <span className="text-primary ml-2">✓</span>}
              </Menu.Item>
            ))}
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}