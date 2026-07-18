import { FormEvent, useEffect, useState } from "react";
import { Category, ExpenseInput } from "../api";

interface Props {
  categories: Category[];
  onCreate: (input: ExpenseInput) => Promise<void>;
}

export function ExpenseForm({ categories, onCreate }: Props) {
  const [amount, setAmount] = useState("");
  const [categoryId, setCategoryId] = useState<number | "">("");
  const [description, setDescription] = useState("");
  const [occurredAt, setOccurredAt] = useState(() => new Date().toISOString().slice(0, 16));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (categoryId === "" && categories.length > 0) {
      setCategoryId(categories[0].id);
    }
  }, [categories, categoryId]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const parsedAmount = Number(amount);
    if (!parsedAmount || parsedAmount <= 0) {
      setError("Amount must be a positive number");
      return;
    }
    if (categoryId === "") {
      setError("Add a category first");
      return;
    }
    setSubmitting(true);
    try {
      await onCreate({
        amount: parsedAmount,
        category_id: categoryId,
        description: description || null,
        occurred_at: new Date(occurredAt).toISOString(),
      });
      setAmount("");
      setDescription("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create expense");
    } finally {
      setSubmitting(false);
    }
  }

  if (categories.length === 0) {
    return (
      <div className="card">
        <h2>Log an expense</h2>
        <p>Add a category first (see "Categories" below) before logging an expense.</p>
      </div>
    );
  }

  return (
    <form className="card" onSubmit={handleSubmit}>
      <h2>Log an expense</h2>
      <div className="form-row">
        <label>
          Amount
          <input
            type="number"
            step="0.01"
            min="0.01"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </label>
        <label>
          Category
          <select value={categoryId} onChange={(e) => setCategoryId(Number(e.target.value))}>
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Date/time
          <input
            type="datetime-local"
            value={occurredAt}
            onChange={(e) => setOccurredAt(e.target.value)}
            required
          />
        </label>
      </div>
      <label>
        Description
        <input
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="optional"
        />
      </label>
      {error && <p className="error-text">{error}</p>}
      <button type="submit" disabled={submitting}>
        {submitting ? "Saving..." : "Add expense"}
      </button>
    </form>
  );
}
