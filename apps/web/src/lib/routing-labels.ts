// Display labels mirroring the server scene dictionary. Unknown ids stay visible so a
// dictionary rollout never hides a classification result.
export const CATEGORY_LABELS: Record<string, string> = {
  business_channel: "经营与渠道",
  marketing_member: "营销与会员",
  ecommerce_product: "电商与产品",
  trade_fulfillment: "交易与履约",
  product_inventory: "商品与库存",
  region_store: "区域与门店",
  data_semantics: "数据与语义",
  report_collaboration: "报告与协作",
  platform_runtime: "平台运行",
  input_boundary: "输入与执行边界",
};

export const SCENE_LABELS: Record<string, string> = {
  "SC-01": "指标快查",
  "SC-02": "趋势比较",
  "SC-03": "销售下降诊断",
  "SC-04": "促销贡献拆解",
  "SC-05": "成熟退款回报",
  "SC-06": "获客比较",
  "SC-07": "投放建议",
  "SC-08": "漏斗查看",
  "SC-09": "转化诊断",
  "SC-10": "订单对账",
  "SC-11": "缺码盘点",
  "SC-12": "调拨建议",
  "SC-13": "库存老化",
  "SC-14": "需求预测",
  "SC-15": "新老店拆解",
  "SC-16": "门店排名与目标差距",
  "SC-17": "复购与分群",
  "SC-18": "实验评估",
  "SC-19": "退货诊断",
  "SC-20": "履约与补货延迟",
  "SC-21": "指标解释",
  "SC-22": "SQL 生成",
  "SC-23": "质量检查",
  "SC-24": "数据整理",
  "SC-25": "图表生成",
  "SC-26": "报告组织",
  "SC-27": "结论核对",
  "SC-28": "会员活动复盘",
  "SC-29": "周期任务",
  "SC-30": "监测草案",
  "SC-31": "运行记录分析",
  "SC-32": "流程改进建议",
  "SC-33": "缺信息",
  "SC-34": "多轮指代",
  "SC-35": "数据不足",
  "SC-36": "外部动作",
  "SC-37": "越权请求",
  "SC-38": "管理操作",
  "SC-39": "范围外",
  "SC-40": "复合任务",
};

export function categoryLabel(id: string | null | undefined): string {
  if (!id) return "";
  return CATEGORY_LABELS[id] ?? id;
}

export function sceneLabel(id: string | null | undefined): string {
  if (!id) return "";
  return SCENE_LABELS[id] ?? id;
}
