"use client";

import React, { FC } from "react";
import { Select } from "antd";
import type { SelectProps } from "antd";

export interface ModoSelectProps extends SelectProps {}

interface ModoSelectInternal extends FC<ModoSelectProps> {
  Option: typeof Select.Option;
}

/** ModoSelect — 默认 filled 变体 + 100% 宽（对齐 data-synth） */
export const ModoSelect: ModoSelectInternal = ({
  variant = "filled",
  placeholder = "请选择",
  allowClear = true,
  style,
  ...props
}) => {
  return (
    <Select
      variant={variant}
      placeholder={placeholder}
      allowClear={allowClear}
      style={{ width: "100%", ...style }}
      {...props}
    />
  );
};

ModoSelect.Option = Select.Option;

export default ModoSelect;