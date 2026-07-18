import { useState } from "react";
import { Category, ConflictError, Expense, ExpenseInput } from "../api";

interface Props {
  expenses: Expense[];
  categories: Category[];
  onUpdate: (id: number, input: ExpenseInput & { version: number }) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onConflict: () => void;
}

export function ExpenseTable({ expenses, categories, onUpdate, onDelete, onConflict }: Props) {
  const [editingId, setEditingId] = useState<number | null>(null);
  const [draft, setDraft] = useState<{ amount: string; categoryId: number; description: string }>({
    amount: "",
    categoryId: 0,
    description: "",
  });
  const [rowError, setRowError] = useState<string | null>(null);

  function startEdit(expense: Expense) {
    setEditingId(expense.id);
    setDraft({
      amount: expense.amount,
      categoryId: expense.category.id,
      description: expense.description || "",
    });
    setRowError(null);
  }

  async function saveEdit(expense: Expense) {
    setRowError(null);
    try {
      await onUpdate(expense.id, {
        amount: Number(draft.amount),
        category_id: draft.categoryId,
        description: draft.description || null,
        occurred_at: expense.occurred_at,
        version: expense.version,
      });
      setEditingId(null);
    } catch (err) {
      if (err instanceof ConflictError) {
        setRowError(err.message);
        onConflict();
      } else {
        setRowError(err instanceof Error ? err.message : "Update failed");
      }
    }
  }

  return (
    <div className="card">
      <h2>Expenses</h2>
      <table>
        <thead>
          <tr>
            <th>Date</th>
            <th>Category</th>
            <th>Amount</th>
            <th>Description</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {expenses.map((expense) => (
            <tr key={expense.id}>
              {editingId === expense.id ? (
                <>
                  <td>{new Date(expense.occurred_at).toLocaleString()}</td>
                  <td>
                    <select
                      aria-label="Category"
                      value={draft.categoryId}
                      onChange={(e) => setDraft({ ...draft, categoryId: Number(e.target.value) })}
                    >
                      {categories.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.01"
                      value={draft.amount}
                      onChange={(e) => setDraft({ ...draft, amount: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      value={draft.description}
                      onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                    />
                  </td>
                  <td className="row-actions">
                    <button onClick={() => saveEdit(expense)}>Save</button>
                    <button onClick={() => setEditingId(null)}>Cancel</button>
                  </td>
                </>
              ) : (
                <>
                  <td>{new Date(expense.occurred_at).toLocaleString()}</td>
                  <td>{expense.category.name}</td>
                  <td>${Number(expense.amount).toFixed(2)}</td>
                  <td>{expense.description}</td>
                  <td className="row-actions">
                    <button onClick={() => startEdit(expense)}>Edit</button>
                    <button onClick={() => onDelete(expense.id)}>Delete</button>
                  </td>
                </>
              )}
            </tr>
          ))}
          {expenses.length === 0 && (
            <tr>
              <td colSpan={5}>No expenses yet.</td>
            </tr>
          )}
        </tbody>
      </table>
      {rowError && <p className="error-text">{rowError}</p>}
    </div>
  );
}
