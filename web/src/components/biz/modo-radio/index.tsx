"use client";

import React from "react";
import { Radio } from "antd";
import styles from "./modo-radio.module.css";

export type ModoRadioProps = React.ComponentProps<typeof Radio>;
export type ModoRadioGroupProps = React.ComponentProps<typeof Radio.Group>;
export type ModoRadioButtonProps = React.ComponentProps<typeof Radio.Button>;

interface ModoRadioInternal extends React.FC<ModoRadioProps> {
  Group: React.FC<ModoRadioGroupProps>;
  Button: React.FC<ModoRadioButtonProps>;
}

const ModoRadioComponent: React.FC<ModoRadioProps> = ({ className, children, ...props }) => {
  return (
    <Radio className={`${styles["modo-radio"]} ${className || ""}`} {...props}>
      {children}
    </Radio>
  );
};

const ModoRadioGroup: React.FC<ModoRadioGroupProps> = ({ children, ...props }) => {
  return <Radio.Group {...props}>{children}</Radio.Group>;
};

const ModoRadioButton: React.FC<ModoRadioButtonProps> = ({ children, ...props }) => {
  return <Radio.Button {...props}>{children}</Radio.Button>;
};

export const ModoRadio = ModoRadioComponent as ModoRadioInternal;
ModoRadio.Group = ModoRadioGroup;
ModoRadio.Button = ModoRadioButton;

export default ModoRadio;