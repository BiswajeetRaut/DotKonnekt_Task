import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Expense } from "../api";

export function SpendingChart({ expenses }: { expenses: Expense[] }) {
  const data = useMemo(() => {
    const totals = new Map<string, number>();
    for (const expense of expenses) {
      totals.set(expense.category, (totals.get(expense.category) || 0) + Number(expense.amount));
    }
    return Array.from(totals.entries()).map(([category, total]) => ({
      category,
      total: Math.round(total * 100) / 100,
    }));
  }, [expenses]);

  return (
    <div className="card">
      <h2>Spending by category</h2>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <BarChart data={data}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis dataKey="category" />
            <YAxis />
            <Tooltip formatter={(value: number) => `$${value.toFixed(2)}`} />
            <Bar dataKey="total" fill="#4f46e5" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
