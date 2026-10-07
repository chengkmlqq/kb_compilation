"use client";

import React from "react";
import styles from "./modo-container.module.css";

export interface ModoContainerProps {
  className?: string;
  style?: React.CSSProperties;
  width?: number | string;
  height?: number | string;
  contentClassName?: string;
  contentStyle?: React.CSSProperties;
  children?: React.ReactNode;
}

/** ModoContainer — 页面级容器（对齐 data-synth）：外层灰底留白，内层白底圆角一屏内容区 */
export const ModoContainer: React.FC<ModoContainerProps> = ({
  className,
  style,
  width = "100%",
  height = "100%",
  contentClassName,
  contentStyle,
  children,
}) => {
  const wrapperStyle: React.CSSProperties = {
    width,
    height,
    ...style,
  };
  return (
    <div className={`${styles["container-wrapper"]} ${className || ""}`} style={wrapperStyle}>
      <div className={`${styles["container-content"]} ${contentClassName || ""}`} style={contentStyle}>
        {children}
      </div>
    </div>
  );
};

export default ModoContainer;