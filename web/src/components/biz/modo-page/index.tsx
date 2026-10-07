"use client";

import React from "react";
import ModoContainer, { ModoContainerProps } from "@/components/biz/modo-container";

export interface ModoPageProps extends Omit<ModoContainerProps, "width" | "height"> {}

/** ModoPage — 列表页外壳（对齐 data-synth modo-page） */
export const ModoPage: React.FC<ModoPageProps> = (props) => {
  return <ModoContainer width="100%" height="100%" {...props} />;
};

export default ModoPage;