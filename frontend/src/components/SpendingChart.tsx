import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Expense } from "../api";

export function SpendingChart({ expenses }: { expenses: Expense[] }) {
  const data = useMemo(() => {
    const totals = new Map<string, number>();
    for (const expense of expenses) {
      const name = expense.category.name;
      totals.set(name, (totals.get(name) || 0) + Number(expense.amount));
    }
    return Array.from(totals.entries()).map(([category, total]) => ({
      category,
      total: Math.round(total * 100) / 100,
    }));
  }, [expenses]);

  return (
    <div className="card">
      <h2>Spending by category</h2>
      <div style={{ width: "100%", height: 320 }}>
        <ResponsiveContainer>
          <BarChart data={data} margin={{ bottom: 40 }}>
            <CartesianGrid strokeDasharray="3 3" />
            {/* interval={0} forces every category to get a label — Recharts'
                default silently drops labels it thinks would overlap, which
                with 5+ categories in this card's width means some bars end
                up unlabeled (confirmed while testing with 6 categories).
                Angling the text is what actually gives them room to fit. */}
            <XAxis dataKey="category" interval={0} angle={-35} textAnchor="end" height={60} tick={{ fontSize: 12 }} />
            <YAxis />
            <Tooltip formatter={(value: number) => `$${value.toFixed(2)}`} />
            <Bar dataKey="total" fill="#4f46e5" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
