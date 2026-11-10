import { useState } from "react";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCorners,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import type { Column, Order, OrderStatus, Priority } from "./api";
import { columnOrders, matches, positionFor } from "./model";

const PRIO_LABEL: Record<Priority, string> = { niedrig: "Niedrig", normal: "Normal", hoch: "Hoch", kritisch: "Kritisch" };

function Card({
  order,
  columns,
  canEdit,
  pending,
  selected,
  onSelect,
  onMove,
}: {
  order: Order;
  columns: Column[];
  canEdit: boolean;
  pending: boolean;
  selected: boolean;
  onSelect: (o: Order) => void;
  onMove: (o: Order, status: OrderStatus) => void;
}) {
  const sortable = useSortable({ id: order.id, disabled: !canEdit || pending });
  const index = columns.findIndex((c) => c.id === order.status);
  const today = new Date().toISOString().slice(0, 10);
  const overdue = !!order.frist && order.frist < today && order.status !== "erledigt";
  const done = order.checkliste.filter((c) => c.erledigt).length;
  return (
    <article
      ref={sortable.setNodeRef}
      style={{ transform: CSS.Transform.toString(sortable.transform), transition: sortable.transition }}
      className={`cc-card prio-${order.prioritaet} ${sortable.isDragging ? "dragging" : ""} ${selected ? "selected" : ""} ${pending ? "pending" : ""}`}
      aria-label={`Auftrag ${order.titel}, ${PRIO_LABEL[order.prioritaet]}`}
    >
      <button type="button" className="cc-card-main" onClick={() => onSelect(order)} {...(canEdit ? sortable.attributes : {})}
        {...(canEdit ? sortable.listeners : {})}>
        <span className="cc-card-title">{order.titel}</span>
        <span className="cc-card-meta">
          {order.kunde && <span>{order.kunde}</span>}
          <span className={`cc-prio prio-${order.prioritaet}`}>{PRIO_LABEL[order.prioritaet]}</span>
          {order.frist && <span className={overdue ? "cc-overdue" : ""}>{overdue ? "überfällig · " : "bis "}{order.frist}</span>}
          {order.checkliste.length > 0 && <span>{done}/{order.checkliste.length} ✓</span>}
          {order.jobs.length > 0 && <span>{order.jobs.length} Job{order.jobs.length > 1 ? "s" : ""}</span>}
        </span>
      </button>
      {canEdit && (
        <div className="cc-card-move">
          <button type="button" aria-label={`${order.titel} nach links verschieben`} disabled={index <= 0 || pending}
            onClick={() => onMove(order, columns[index - 1].id)}>‹</button>
          <button type="button" aria-label={`${order.titel} nach rechts verschieben`}
            disabled={index >= columns.length - 1 || pending} onClick={() => onMove(order, columns[index + 1].id)}>›</button>
        </div>
      )}
    </article>
  );
}

function Lane({ column, children, count }: { column: Column; children: React.ReactNode; count: number }) {
  const drop = useDroppable({ id: `col:${column.id}` });
  return (
    <section ref={drop.setNodeRef} className={`cc-lane ${drop.isOver ? "over" : ""}`} aria-label={`Spalte ${column.titel}`}>
      <header>
        <h3>{column.titel}</h3>
        <span className="cc-count">{count}</span>
      </header>
      <div className="cc-lane-body">{children}</div>
    </section>
  );
}

export function KanbanPanel({
  columns,
  orders,
  pending,
  query,
  canEdit,
  selectedId,
  onSelect,
  onMove,
  onCreate,
  error,
}: {
  columns: Column[];
  orders: Order[];
  pending: Record<string, Order>;
  query: string;
  canEdit: boolean;
  selectedId: string | null;
  onSelect: (o: Order) => void;
  onMove: (order: Order, status: OrderStatus, position: number) => void;
  onCreate: (fields: Partial<Order>) => Promise<void>;
  error: string | null;
}) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const [title, setTitle] = useState("");
  const [customer, setCustomer] = useState("");
  const [prio, setPrio] = useState<Priority>("normal");
  const [busy, setBusy] = useState(false);
  const visible = orders.filter((o) => matches(o, query));

  const end = (event: DragEndEvent) => {
    const order = orders.find((o) => o.id === event.active.id);
    const overId = event.over?.id ? String(event.over.id) : null;
    if (!order || !overId) return;
    const target = overId.startsWith("col:") ? (overId.slice(4) as OrderStatus) : orders.find((o) => o.id === overId)?.status;
    if (!target) return;
    const before = overId.startsWith("col:") ? null : overId;
    if (target === order.status && (before === null || before === order.id)) return;
    onMove(order, target, positionFor(orders, target, before, order.id));
  };
  const moveTo = (order: Order, status: OrderStatus) => onMove(order, status, positionFor(orders, status, null, order.id));

  return (
    <div className="cc-kanban">
      {canEdit && (
        <form
          className="cc-new"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!title.trim() || busy) return;
            setBusy(true);
            try {
              await onCreate({ titel: title.trim(), kunde: customer.trim(), prioritaet: prio });
              setTitle("");
              setCustomer("");
              setPrio("normal");
            } finally {
              setBusy(false);
            }
          }}
        >
          <input aria-label="Titel des neuen Auftrags" placeholder="Neuer Auftrag …" value={title} maxLength={200}
            onChange={(e) => setTitle(e.target.value)} />
          <input aria-label="Kunde" placeholder="Kunde" value={customer} maxLength={120} onChange={(e) => setCustomer(e.target.value)} />
          <select aria-label="Priorität" value={prio} onChange={(e) => setPrio(e.target.value as Priority)}>
            {Object.entries(PRIO_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          <button type="submit" className="cc-btn accent" disabled={!title.trim() || busy}>Anlegen</button>
        </form>
      )}
      {error && <div className="cc-error" role="alert">{error}</div>}
      <DndContext sensors={sensors} collisionDetection={closestCorners} onDragEnd={end}>
        <div className="cc-lanes">
          {columns.map((column) => {
            const items = columnOrders(visible, column.id);
            return (
              <Lane key={column.id} column={column} count={items.length}>
                <SortableContext items={items.map((o) => o.id)} strategy={verticalListSortingStrategy}>
                  {items.map((order) => (
                    <Card key={order.id} order={order} columns={columns} canEdit={canEdit} pending={!!pending[order.id]}
                      selected={selectedId === order.id} onSelect={onSelect} onMove={moveTo} />
                  ))}
                </SortableContext>
                {items.length === 0 && <p className="cc-empty">Leer</p>}
              </Lane>
            );
          })}
        </div>
      </DndContext>
      <p className="cc-hint">Ziehen oder ‹ › verschiebt nur die Karte. Es startet dabei keinen Auftrag an Jarvis.</p>
    </div>
  );
}
