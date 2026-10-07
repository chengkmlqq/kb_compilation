"use client";

import React from "react";
import { Input as AntInput } from "antd";
import type { InputProps, InputRef } from "antd";
import styles from "./modo-input.module.css";

const { TextArea, Search, Password } = AntInput;

export interface ModoInputProps extends InputProps {}

/** ModoInput — 浅灰填充风格输入框（对齐 data-synth）：背景 #EFF4F9，focus 白底蓝边 */
export const ModoInput = React.forwardRef<InputRef, ModoInputProps>(({ className, ...props }, ref) => {
  return <AntInput ref={ref} className={`${styles["modo-input"]} ${className || ""}`} {...props} />;
});
ModoInput.displayName = "ModoInput";

export const ModoTextArea = React.forwardRef<any, React.ComponentProps<typeof TextArea>>(
  ({ className, ...props }, ref) => {
    return <TextArea ref={ref} className={`${styles["modo-input"]} ${className || ""}`} {...props} />;
  }
);
ModoTextArea.displayName = "ModoTextArea";

export const ModoSearch = React.forwardRef<InputRef, React.ComponentProps<typeof Search>>(
  ({ className, ...props }, ref) => {
    return <Search ref={ref} className={`${styles["modo-input"]} ${className || ""}`} {...props} />;
  }
);
ModoSearch.displayName = "ModoSearch";

export const ModoPassword = React.forwardRef<InputRef, React.ComponentProps<typeof Password>>(
  ({ className, ...props }, ref) => {
    return <Password ref={ref} className={`${styles["modo-input"]} ${className || ""}`} {...props} />;
  }
);
ModoPassword.displayName = "ModoPassword";

export default ModoInput;