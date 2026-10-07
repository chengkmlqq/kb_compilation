"use client";

import React from "react";
import { Button } from "antd";
import type { ButtonProps } from "antd";
import styles from "./modo-button.module.css";

export interface ModoButtonProps extends Omit<ButtonProps, "color"> {
  children?: React.ReactNode;
  extraLarge?: boolean;
  color?: ButtonProps["color"] | string;
}

const getSizeStyles = (size?: ButtonProps["size"], extraLarge?: boolean): React.CSSProperties => {
  if (extraLarge) {
    return {
      height: "36px",
      padding: "0 20px",
      fontSize: "14px",
      lineHeight: "34px",
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
    };
  }
  switch (size) {
    case "small":
      return {
        height: "24px",
        padding: "0 8px",
        fontSize: "12px",
        lineHeight: "22px",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      };
    case "large":
      return {
        height: "32px",
        padding: "0 16px",
        fontSize: "14px",
        lineHeight: "30px",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      };
    case "middle":
    default:
      return {
        height: "28px",
        padding: "0 12px",
        fontSize: "12px",
        lineHeight: "26px",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
      };
  }
};

/** ModoButton — 扁平风格按钮（对齐 data-synth）：默认 filled 灰底、shape=round、定高定字号 */
export const ModoButton = React.forwardRef<HTMLButtonElement | HTMLAnchorElement, ModoButtonProps>(
  ({ shape = "round", size, extraLarge, style, color, children, ...props }, ref) => {
    const sizeStyles = getSizeStyles(size, extraLarge);
    const isIconButton = !!props.icon && !children;
    if (isIconButton) {
      sizeStyles.padding = "0";
      const sizeValues = { small: "24px", medium: "28px", middle: "28px", large: "32px" } as const;
      const height = extraLarge ? "36px" : sizeValues[(size || "middle") as keyof typeof sizeValues];
      (sizeStyles as Record<string, unknown>).width = height;
    }
    const isTextOrLink = props.type === "text" || props.type === "link";
    const isCircle = shape === "circle" || !!(props.className && props.className.includes("ant-btn-circle"));
    if (isCircle) sizeStyles.padding = "0";
    const minWidthStyle = !isIconButton && !isTextOrLink && !isCircle ? { minWidth: "72px" } : {};

    const defaultVariantProps: Record<string, unknown> = {};
    if (!props.type && !(props as Record<string, unknown>).variant && !props.danger) {
      defaultVariantProps.variant = "filled";
      defaultVariantProps.color = color || "default";
    }

    return (
      <Button
        ref={ref}
        {...defaultVariantProps}
        {...props}
        className={`${styles.modoButton} ${props.className || ""}`}
        shape={shape}
        size={size}
        color={(color || defaultVariantProps.color) as ButtonProps["color"]}
        style={{ ...sizeStyles, ...minWidthStyle, ...style }}
      >
        {children}
      </Button>
    );
  }
);

ModoButton.displayName = "ModoButton";

export default ModoButton;