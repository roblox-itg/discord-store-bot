import os
import sqlite3
import random
import string
from datetime import datetime, timedelta

from dotenv import load_dotenv
import discord
from discord import app_commands
from discord.ext import commands, tasks

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
ADMIN_CHANNEL_ID = int(os.getenv("ADMIN_CHANNEL_ID", "0"))
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", "0"))
ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "Admin")
HELPER_ROLE_NAME = os.getenv("HELPER_ROLE_NAME", "Helper")

DB_NAME = "store.db"

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================================================
# DATABASE
# =========================================================
def get_conn():
    return sqlite3.connect(DB_NAME)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            price INTEGER NOT NULL,
            stock INTEGER NOT NULL DEFAULT 0,
            description TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_code TEXT NOT NULL UNIQUE,
            user_id TEXT NOT NULL,
            username TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            product_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price INTEGER NOT NULL,
            total_price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'UNPAID',
            created_at TEXT NOT NULL,
            due_at TEXT NOT NULL,
            paid_at TEXT,
            notes TEXT,
            handled_by TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT NOT NULL,
            actor_name TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_value TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


# =========================================================
# HELPERS
# =========================================================
def now_dt():
    return datetime.now()


def now_str():
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def rupiah(value: int) -> str:
    return f"Rp{value:,}".replace(",", ".")


def generate_invoice_code() -> str:
    date_part = now_dt().strftime("%Y%m%d")
    rand_part = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"INV-{date_part}-{rand_part}"


def member_has_role(member: discord.Member, role_name: str) -> bool:
    return any(role.name == role_name for role in member.roles)


def is_admin_member(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or member_has_role(member, ADMIN_ROLE_NAME)


def is_helper_member(member: discord.Member) -> bool:
    return is_admin_member(member) or member_has_role(member, HELPER_ROLE_NAME)


def actor_role(member: discord.Member) -> str:
    if is_admin_member(member):
        return "ADMIN"
    if is_helper_member(member):
        return "HELPER"
    return "USER"


def log_activity(actor_id: str, actor_name: str, actor_role_name: str,
                 action_type: str, target_type: str, target_value: str, detail: str = ""):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO activity_logs (
            actor_id, actor_name, actor_role, action_type,
            target_type, target_value, detail, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        actor_id, actor_name, actor_role_name, action_type,
        target_type, target_value, detail, now_str()
    ))
    conn.commit()
    conn.close()


async def send_admin_log(content=None, embed=None):
    channel = bot.get_channel(ADMIN_CHANNEL_ID)
    if channel:
        await channel.send(content=content, embed=embed)


def get_invoice_detail(invoice_code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT invoice_code, username, product_name, quantity,
               unit_price, total_price, status, created_at,
               due_at, paid_at, notes, handled_by
        FROM invoices
        WHERE invoice_code = ?
    """, (invoice_code,))
    row = cur.fetchone()
    conn.close()
    return row


def get_pending_invoices(limit=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT invoice_code, username, product_name, quantity,
               total_price, status, due_at
        FROM invoices
        WHERE status IN ('UNPAID', 'PROCESSING')
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_dashboard_data():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM products")
    total_products = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(stock), 0) FROM products")
    total_stock = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices")
    total_invoices = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'UNPAID'")
    unpaid = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'PROCESSING'")
    processing = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'PAID'")
    paid = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'DONE'")
    done = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'EXPIRED'")
    expired = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM invoices WHERE status = 'CANCELLED'")
    cancelled = cur.fetchone()[0]

    cur.execute("SELECT COALESCE(SUM(total_price), 0) FROM invoices WHERE status IN ('PAID', 'DONE')")
    revenue = cur.fetchone()[0]

    conn.close()

    return {
        "total_products": total_products,
        "total_stock": total_stock,
        "total_invoices": total_invoices,
        "unpaid": unpaid,
        "processing": processing,
        "paid": paid,
        "done": done,
        "expired": expired,
        "cancelled": cancelled,
        "revenue": revenue,
    }


def build_dashboard_embed():
    data = get_dashboard_data()
    embed = discord.Embed(
        title="Dashboard Bot Toko",
        color=discord.Color.gold(),
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Total Produk", value=str(data["total_products"]), inline=True)
    embed.add_field(name="Total Stok", value=str(data["total_stock"]), inline=True)
    embed.add_field(name="Total Invoice", value=str(data["total_invoices"]), inline=True)
    embed.add_field(name="UNPAID", value=str(data["unpaid"]), inline=True)
    embed.add_field(name="PROCESSING", value=str(data["processing"]), inline=True)
    embed.add_field(name="PAID", value=str(data["paid"]), inline=True)
    embed.add_field(name="DONE", value=str(data["done"]), inline=True)
    embed.add_field(name="EXPIRED", value=str(data["expired"]), inline=True)
    embed.add_field(name="CANCELLED", value=str(data["cancelled"]), inline=True)
    embed.add_field(name="Revenue", value=rupiah(data["revenue"]), inline=False)
    embed.set_footer(text="Gunakan tombol refresh untuk update data terbaru")
    return embed


def build_pending_embed(limit=15):
    rows = get_pending_invoices(limit)
    embed = discord.Embed(
        title="Pending / Processing Invoice",
        color=discord.Color.orange(),
        timestamp=discord.utils.utcnow()
    )

    if not rows:
        embed.description = "Tidak ada invoice pending."
        return embed

    for code, username, product_name, qty, total, status, due_at in rows:
        embed.add_field(
            name=f"{code} | {status}",
            value=f"{username}\n{product_name} x{qty}\n{rupiah(total)}\nDue: {due_at}",
            inline=False
        )
    return embed


def build_invoice_embed(row):
    (
        invoice_code, username, product_name, quantity,
        unit_price, total_price, status, created_at,
        due_at, paid_at, notes, handled_by
    ) = row

    color = discord.Color.orange()
    if status == "PAID":
        color = discord.Color.green()
    elif status == "DONE":
        color = discord.Color.dark_green()
    elif status == "PROCESSING":
        color = discord.Color.blurple()
    elif status == "CANCELLED":
        color = discord.Color.red()
    elif status == "EXPIRED":
        color = discord.Color.dark_red()

    embed = discord.Embed(title="Detail Invoice", color=color)
    embed.add_field(name="No. Invoice", value=invoice_code, inline=False)
    embed.add_field(name="Customer", value=username, inline=False)
    embed.add_field(name="Produk", value=product_name, inline=False)
    embed.add_field(name="Qty", value=str(quantity), inline=True)
    embed.add_field(name="Harga Satuan", value=rupiah(unit_price), inline=True)
    embed.add_field(name="Total", value=rupiah(total_price), inline=False)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Dibuat", value=created_at, inline=True)
    embed.add_field(name="Batas Bayar", value=due_at, inline=True)
    embed.add_field(name="Paid At", value=paid_at if paid_at else "-", inline=False)
    embed.add_field(name="Ditangani Oleh", value=handled_by if handled_by else "-", inline=False)
    embed.add_field(name="Catatan", value=notes if notes else "-", inline=False)
    return embed


def update_invoice_status(invoice_code: str, new_status: str, handler: str, notes: str | None = None):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("SELECT status FROM invoices WHERE invoice_code = ?", (invoice_code,))
    existing = cur.fetchone()
    if not existing:
        conn.close()
        return 0

    paid_at = None
    if new_status == "PAID":
        paid_at = now_str()

    if notes is None:
        cur.execute("""
            UPDATE invoices
            SET status = ?, handled_by = ?, paid_at = COALESCE(?, paid_at)
            WHERE invoice_code = ?
        """, (new_status, handler, paid_at, invoice_code))
    else:
        cur.execute("""
            UPDATE invoices
            SET status = ?, handled_by = ?, notes = ?, paid_at = COALESCE(?, paid_at)
            WHERE invoice_code = ?
        """, (new_status, handler, notes, paid_at, invoice_code))

    changed = cur.rowcount
    conn.commit()
    conn.close()
    return changed


def confirm_payment_and_reduce_stock(invoice_code: str, handler: str):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, user_id, username, product_id, product_name, quantity, total_price, status
        FROM invoices
        WHERE invoice_code = ?
    """, (invoice_code,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return {"ok": False, "message": "Invoice tidak ditemukan."}

    invoice_id, user_id, username, product_id, product_name, quantity, total_price, status = row

    if status in ("PAID", "DONE"):
        conn.close()
        return {"ok": False, "message": "Invoice sudah dibayar/diselesaikan."}

    if status in ("CANCELLED", "EXPIRED"):
        conn.close()
        return {"ok": False, "message": "Invoice sudah tidak aktif."}

    cur.execute("SELECT stock FROM products WHERE id = ?", (product_id,))
    product = cur.fetchone()
    if not product:
        conn.close()
        return {"ok": False, "message": "Produk tidak ditemukan."}

    stock = product[0]
    if stock < quantity:
        conn.close()
        return {"ok": False, "message": f"Stok tidak cukup. Stok sekarang: {stock}"}

    new_stock = stock - quantity
    paid_at = now_str()

    try:
        cur.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
        cur.execute("""
            UPDATE invoices
            SET status = 'PAID', paid_at = ?, handled_by = ?
            WHERE id = ?
        """, (paid_at, handler, invoice_id))
        conn.commit()
        conn.close()

        return {
            "ok": True,
            "user_id": user_id,
            "username": username,
            "product_name": product_name,
            "quantity": quantity,
            "total_price": total_price,
            "new_stock": new_stock
        }
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"ok": False, "message": str(e)}


def expire_due_invoices():
    conn = get_conn()
    cur = conn.cursor()
    now_value = now_str()

    cur.execute("""
        SELECT invoice_code, user_id
        FROM invoices
        WHERE status IN ('UNPAID', 'PROCESSING')
          AND due_at < ?
    """, (now_value,))
    rows = cur.fetchall()

    expired_codes = []
    for invoice_code, _user_id in rows:
        cur.execute("""
            UPDATE invoices
            SET status = 'EXPIRED'
            WHERE invoice_code = ?
        """, (invoice_code,))
        expired_codes.append(invoice_code)

    conn.commit()
    conn.close()
    return expired_codes


def get_recent_logs(limit=10):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT actor_name, actor_role, action_type, target_type, target_value, detail, created_at
        FROM activity_logs
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return rows


def build_logs_embed(limit=10):
    rows = get_recent_logs(limit)
    embed = discord.Embed(
        title="Aktivitas Terbaru",
        color=discord.Color.light_grey(),
        timestamp=discord.utils.utcnow()
    )
    if not rows:
        embed.description = "Belum ada aktivitas."
        return embed

    for actor_name, role_name, action_type, target_type, target_value, detail, created_at in rows:
        embed.add_field(
            name=f"{actor_name} [{role_name}]",
            value=f"{action_type} • {target_type}: {target_value}\n{detail or '-'}\n{created_at}",
            inline=False
        )
    return embed


def get_all_products():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, price, stock, description
        FROM products
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()
    return rows


def get_product_by_id(product_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, price, stock, description
        FROM products
        WHERE id = ?
    """, (product_id,))
    row = cur.fetchone()
    conn.close()
    return row


def build_member_order_embed():
    products = get_all_products()

    embed = discord.Embed(
        title="Panel Order Member",
        description="Pilih produk dari dropdown di bawah untuk membuat order lebih cepat.",
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow()
    )

    if not products:
        embed.add_field(
            name="Belum ada produk",
            value="Admin belum menambahkan produk.",
            inline=False
        )
        return embed

    preview = products[:10]
    for _product_id, name, price, stock, description in preview:
        embed.add_field(
            name=f"{name} | {rupiah(price)}",
            value=f"Stok: **{stock}**\n{description or '-'}",
            inline=False
        )

    if len(products) > 10:
        embed.set_footer(text=f"Menampilkan 10 dari {len(products)} produk")
    else:
        embed.set_footer(text="Pilih produk dari dropdown untuk order")

    return embed


# =========================================================
# MODALS
# =========================================================
class AddProductModal(discord.ui.Modal, title="Tambah Produk"):
    nama = discord.ui.TextInput(label="Nama Produk", max_length=100)
    harga = discord.ui.TextInput(label="Harga", placeholder="50000")
    stok = discord.ui.TextInput(label="Stok", placeholder="10")
    deskripsi = discord.ui.TextInput(label="Deskripsi", required=False, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Kamu tidak punya akses admin.", ephemeral=True)
            return

        try:
            harga_int = int(str(self.harga))
            stok_int = int(str(self.stok))
        except ValueError:
            await interaction.response.send_message("Harga dan stok harus angka.", ephemeral=True)
            return

        try:
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO products (name, price, stock, description)
                VALUES (?, ?, ?, ?)
            """, (str(self.nama), harga_int, stok_int, str(self.deskripsi)))
            conn.commit()
            conn.close()

            log_activity(
                str(member.id), str(member), actor_role(member),
                "ADD_PRODUCT", "PRODUCT", str(self.nama),
                f"Harga={harga_int}, Stok={stok_int}"
            )

            await interaction.response.send_message(
                f"✅ Produk **{self.nama}** berhasil ditambahkan.",
                ephemeral=True
            )
        except sqlite3.IntegrityError:
            await interaction.response.send_message("❌ Nama produk sudah ada.", ephemeral=True)


class SetStockModal(discord.ui.Modal, title="Ubah Stok"):
    nama = discord.ui.TextInput(label="Nama Produk")
    stok = discord.ui.TextInput(label="Stok Baru", placeholder="25")

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Kamu tidak punya akses admin.", ephemeral=True)
            return

        try:
            stok_int = int(str(self.stok))
        except ValueError:
            await interaction.response.send_message("Stok harus angka.", ephemeral=True)
            return

        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE products SET stock = ? WHERE LOWER(name)=LOWER(?)", (stok_int, str(self.nama)))
        conn.commit()
        changed = cur.rowcount
        conn.close()

        if changed == 0:
            await interaction.response.send_message("❌ Produk tidak ditemukan.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "SET_STOCK", "PRODUCT", str(self.nama),
            f"Stok baru={stok_int}"
        )

        await interaction.response.send_message(
            f"✅ Stok produk **{self.nama}** diubah menjadi **{stok_int}**.",
            ephemeral=True
        )


class InvoiceLookupModal(discord.ui.Modal, title="Cek Detail Invoice"):
    invoice_code = discord.ui.TextInput(label="Kode Invoice", placeholder="INV-20260228-ABC123")

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Kamu tidak punya akses helper/admin.", ephemeral=True)
            return

        row = get_invoice_detail(str(self.invoice_code))
        if not row:
            await interaction.response.send_message("❌ Invoice tidak ditemukan.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "LOOKUP_INVOICE", "INVOICE", str(self.invoice_code),
            "Melihat detail invoice"
        )

        await interaction.response.send_message(embed=build_invoice_embed(row), ephemeral=True)


class InvoiceActionModal(discord.ui.Modal):
    def __init__(self, title_text: str, target_status: str):
        super().__init__(title=title_text)
        self.target_status = target_status
        self.invoice_code = discord.ui.TextInput(label="Kode Invoice", placeholder="INV-20260228-ABC123")
        self.note = discord.ui.TextInput(label="Catatan", required=False, style=discord.TextStyle.paragraph)
        self.add_item(self.invoice_code)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Kamu tidak punya akses helper/admin.", ephemeral=True)
            return

        changed = update_invoice_status(
            invoice_code=str(self.invoice_code),
            new_status=self.target_status,
            handler=str(member),
            notes=str(self.note) if str(self.note).strip() else None
        )

        if changed == 0:
            await interaction.response.send_message("❌ Invoice tidak ditemukan.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            f"SET_{self.target_status}", "INVOICE", str(self.invoice_code),
            str(self.note) if str(self.note).strip() else "-"
        )

        await interaction.response.send_message(
            f"✅ Invoice **{self.invoice_code}** diubah menjadi **{self.target_status}**.",
            ephemeral=True
        )


class PayInvoiceModal(discord.ui.Modal, title="Konfirmasi Pembayaran"):
    invoice_code = discord.ui.TextInput(label="Kode Invoice", placeholder="INV-20260228-ABC123")

    async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Kamu tidak punya akses helper/admin.", ephemeral=True)
            return

        result = confirm_payment_and_reduce_stock(str(self.invoice_code), str(member))
        if not result["ok"]:
            await interaction.response.send_message(f"❌ {result['message']}", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "CONFIRM_PAYMENT", "INVOICE", str(self.invoice_code),
            f"Produk={result['product_name']}, Qty={result['quantity']}, StokSisa={result['new_stock']}"
        )

        await interaction.response.send_message(
            f"✅ Invoice **{self.invoice_code}** berhasil dikonfirmasi **PAID**.\n"
            f"Stok baru: **{result['new_stock']}**",
            ephemeral=True
        )

        try:
            user = await bot.fetch_user(int(result["user_id"]))
            dm_embed = discord.Embed(title="Pembayaran Diterima", color=discord.Color.green())
            dm_embed.add_field(name="Invoice", value=str(self.invoice_code), inline=False)
            dm_embed.add_field(name="Produk", value=result["product_name"], inline=False)
            dm_embed.add_field(name="Qty", value=str(result["quantity"]), inline=True)
            dm_embed.add_field(name="Total", value=rupiah(result["total_price"]), inline=True)
            dm_embed.add_field(name="Status", value="PAID", inline=True)
            await user.send(embed=dm_embed)
        except Exception:
            pass

        await send_admin_log(
            content=f"💰 Invoice **{self.invoice_code}** dikonfirmasi PAID oleh **{member}**"
        )


class CancelInvoiceModal(discord.ui.Modal, title="Batalkan Invoice"):
    invoice_code = discord.ui.TextInput(label="Kode Invoice", placeholder="INV-20260228-ABC123")
    note = discord.ui.TextInput(label="Alasan Cancel", required=False, style=discord.TextStyle.paragraph)

async def on_submit(self, interaction: discord.Interaction):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Kamu tidak punya akses admin.", ephemeral=True)
            return

        changed = update_invoice_status(
            invoice_code=str(self.invoice_code),
            new_status="CANCELLED",
            handler=str(member),
            notes=str(self.note) if str(self.note).strip() else None
        )

        if changed == 0:
            await interaction.response.send_message("❌ Invoice tidak ditemukan.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "CANCEL_INVOICE", "INVOICE", str(self.invoice_code),
            str(self.note) if str(self.note).strip() else "-"
        )

        await interaction.response.send_message(
            f"✅ Invoice **{self.invoice_code}** dibatalkan.",
            ephemeral=True
        )


class MemberOrderModal(discord.ui.Modal):
    def __init__(self, product_id: int, product_name: str, unit_price: int, stock_value: int):
        super().__init__(title=f"Order: {product_name}")
        self.product_id = product_id
        self.product_name = product_name
        self.unit_price = unit_price
        self.stock_value = stock_value

        self.quantity = discord.ui.TextInput(
            label="Jumlah Order",
            placeholder=f"Maksimal {stock_value}",
            required=True,
            max_length=5
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(str(self.quantity))
        except ValueError:
            await interaction.response.send_message("❌ Jumlah harus berupa angka.", ephemeral=True)
            return

        if qty <= 0:
            await interaction.response.send_message("❌ Jumlah harus lebih dari 0.", ephemeral=True)
            return

        latest_product = get_product_by_id(self.product_id)
        if not latest_product:
            await interaction.response.send_message("❌ Produk sudah tidak tersedia.", ephemeral=True)
            return

        product_id, product_name, unit_price, stock_value, _description = latest_product

        if stock_value < qty:
            await interaction.response.send_message(
                f"❌ Stok tidak cukup. Stok tersedia: **{stock_value}**",
                ephemeral=True
            )
            return

        invoice_code = generate_invoice_code()
        total_price = unit_price * qty
        created_at = now_str()
        due_at = (now_dt() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        conn = get_conn()
        cur = conn.cursor()

        try:
            cur.execute("""
                INSERT INTO invoices (
                    invoice_code, user_id, username, product_id, product_name,
                    quantity, unit_price, total_price, status, created_at, due_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invoice_code,
                str(interaction.user.id),
                str(interaction.user),
                product_id,
                product_name,
                qty,
                unit_price,
                total_price,
                "UNPAID",
                created_at,
                due_at
            ))
            conn.commit()
            conn.close()

            row = get_invoice_detail(invoice_code)
            embed = build_invoice_embed(row)

            log_activity(
                str(interaction.user.id), str(interaction.user), "USER",
                "CREATE_ORDER_PANEL", "INVOICE", invoice_code,
                f"{product_name} x{qty}"
            )

            await interaction.response.send_message(
                f"✅ Order berhasil dibuat.\nInvoice: **{invoice_code}**\nCek DM untuk detail invoice.",
                ephemeral=True
            )

            try:
                await interaction.user.send(
                    content="Berikut invoice pesanan kamu:",
                    embed=embed
                )
            except Exception:
                await interaction.followup.send(
                    "⚠️ Aku tidak bisa kirim DM. Aktifkan DM server ya.",
                    ephemeral=True
                )

            await send_admin_log(
                content=f"🛒 Order baru dari {interaction.user.mention} via panel member",
                embed=embed
            )

        except Exception as e:
            conn.rollback()
            conn.close()
            await interaction.response.send_message(
                f"❌ Gagal membuat order: {e}",
                ephemeral=True
            )

# =========================================================
# SELECTS
# =========================================================
class ProductSelect(discord.ui.Select):
    def __init__(self):
        products = get_all_products()

        options = []
        if products:
            for product_id, name, price, stock, description in products[:25]:
                desc = f"Harga {rupiah(price)} | Stok {stock}"
                if description:
                    desc = f"{desc} | {description[:40]}"
                options.append(
                    discord.SelectOption(
                        label=name[:100],
                        value=str(product_id),
                        description=desc[:100]
                    )
                )
        else:
            options.append(
                discord.SelectOption(
                    label="Belum ada produk",
                    value="0",
                    description="Admin belum menambahkan produk"
                )
            )

        super().__init__(
            placeholder="Pilih produk yang mau dipesan",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="member_product_select"
        )

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            await interaction.response.send_message(
                "Belum ada produk yang bisa dipesan.",
                ephemeral=True
            )
            return

        product_id = int(self.values[0])
        product = get_product_by_id(product_id)

        if not product:
            await interaction.response.send_message(
                "❌ Produk tidak ditemukan.",
                ephemeral=True
            )
            return

        product_id, name, price, stock, _description = product

        if stock <= 0:
            await interaction.response.send_message(
                f"❌ Produk **{name}** sedang habis.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(
            MemberOrderModal(
                product_id=product_id,
                product_name=name,
                unit_price=price,
                stock_value=stock
            )
        )


# =========================================================
# VIEWS
# =========================================================
class AdminPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Dashboard", style=discord.ButtonStyle.primary, custom_id="admin_dashboard")
    async def dashboard(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_dashboard_embed(), ephemeral=True)

    @discord.ui.button(label="Tambah Produk", style=discord.ButtonStyle.success, custom_id="admin_add_product")
    async def add_product(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_modal(AddProductModal())

    @discord.ui.button(label="Set Stok", style=discord.ButtonStyle.secondary, custom_id="admin_set_stock")
    async def set_stock(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_modal(SetStockModal())

    @discord.ui.button(label="Pending", style=discord.ButtonStyle.secondary, custom_id="admin_pending")
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_pending_embed(), ephemeral=True)

    @discord.ui.button(label="Konfirmasi Bayar", style=discord.ButtonStyle.success, custom_id="admin_pay")
    async def pay(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_modal(PayInvoiceModal())

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="admin_cancel")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_modal(CancelInvoiceModal())

    @discord.ui.button(label="Logs", style=discord.ButtonStyle.secondary, custom_id="admin_logs")
    async def logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_logs_embed(), ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, custom_id="admin_refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_admin_member(member):
            await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
            return
        await interaction.response.send_message(
            content="✅ Data terbaru:",
            embed=build_dashboard_embed(),
            ephemeral=True
        )


class HelperPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Pending", style=discord.ButtonStyle.secondary, custom_id="helper_pending")
    async def pending(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "VIEW_PENDING", "INVOICE", "PENDING_LIST",
            "Melihat invoice pending"
        )

        await interaction.response.send_message(embed=build_pending_embed(), ephemeral=True)

    @discord.ui.button(label="Cek Detail", style=discord.ButtonStyle.primary, custom_id="helper_lookup")
    async def lookup(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return
        await interaction.response.send_modal(InvoiceLookupModal())

    @discord.ui.button(label="Diproses", style=discord.ButtonStyle.secondary, custom_id="helper_processing")
    async def processing(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return
        await interaction.response.send_modal(InvoiceActionModal("Tandai Diproses", "PROCESSING"))

    @discord.ui.button(label="Selesai", style=discord.ButtonStyle.success, custom_id="helper_done")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return
        await interaction.response.send_modal(InvoiceActionModal("Tandai Selesai", "DONE"))

@discord.ui.button(label="Konfirmasi Bayar", style=discord.ButtonStyle.success, custom_id="helper_pay")
    async def pay(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return
        await interaction.response.send_modal(PayInvoiceModal())

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, custom_id="helper_refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        if not isinstance(member, discord.Member) or not is_helper_member(member):
            await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
            return

        log_activity(
            str(member.id), str(member), actor_role(member),
            "REFRESH_PANEL", "PANEL", "HELPER_PANEL",
            "Refresh helper panel"
        )

        await interaction.response.send_message(embed=build_pending_embed(), ephemeral=True)


class MemberOrderPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ProductSelect())

    @discord.ui.button(label="Refresh Produk", style=discord.ButtonStyle.primary, custom_id="member_refresh_products")
    async def refresh_products(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            embed=build_member_order_embed(),
            view=MemberOrderPanelView(),
            ephemeral=True
        )

    @discord.ui.button(label="Lihat Pending Invoice Saya", style=discord.ButtonStyle.secondary, custom_id="member_my_invoices")
    async def my_invoices(self, interaction: discord.Interaction, button: discord.ui.Button):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT invoice_code, product_name, quantity, total_price, status, due_at
            FROM invoices
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (str(interaction.user.id),))
        rows = cur.fetchall()
        conn.close()

        embed = discord.Embed(
            title="Invoice Saya",
            color=discord.Color.blurple()
        )

        if not rows:
            embed.description = "Kamu belum punya invoice."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        for code, product_name, qty, total, status, due_at in rows:
            embed.add_field(
                name=f"{code} | {status}",
                value=f"{product_name} x{qty}\n{rupiah(total)}\nDue: {due_at}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
# TASKS
# =========================================================
@tasks.loop(minutes=1)
async def invoice_expiry_loop():
    expired_codes = expire_due_invoices()
    if not expired_codes:
        return

    for code in expired_codes:
        log_activity(
            "SYSTEM", "SYSTEM", "SYSTEM",
            "AUTO_EXPIRE", "INVOICE", code,
            "Invoice expired otomatis"
        )
        await send_admin_log(content=f"⏰ Invoice **{code}** otomatis berubah menjadi **EXPIRED**.")


# =========================================================
# EVENTS
# =========================================================
@bot.event
async def on_ready():
    init_db()

    bot.add_view(AdminPanelView())
    bot.add_view(HelperPanelView())
    bot.add_view(MemberOrderPanelView())

    if not invoice_expiry_loop.is_running():
        invoice_expiry_loop.start()

    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} command(s) to guild {GUILD_ID}")
        else:
            synced = await bot.tree.sync()
            print(f"Globally synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync error: {e}")

    print(f"Bot aktif sebagai {bot.user}")

# =========================================================
# COMMANDS
# =========================================================
@bot.tree.command(name="deploypanels", description="Deploy semua panel ke channel panel")
async def deploypanels(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return

    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("PANEL_CHANNEL_ID tidak valid.", ephemeral=True)
        return

    admin_embed = discord.Embed(
        title="Admin Panel",
        description="Panel kontrol penuh untuk admin.",
        color=discord.Color.red()
    )
    helper_embed = discord.Embed(
        title="Helper Panel",
        description="Panel untuk bantu mengelola invoice/order.",
        color=discord.Color.blurple()
    )
    member_embed = build_member_order_embed()

    await channel.send(embed=admin_embed, view=AdminPanelView())
    await channel.send(embed=helper_embed, view=HelperPanelView())
    await channel.send(embed=member_embed, view=MemberOrderPanelView())

    log_activity(
        str(member.id), str(member), actor_role(member),
        "DEPLOY_PANELS", "CHANNEL", str(PANEL_CHANNEL_ID),
        "Deploy admin, helper, dan member order panel"
    )

    await interaction.response.send_message(
        "✅ Semua panel berhasil dikirim ke channel panel.",
        ephemeral=True
    )


@bot.tree.command(name="deployorderpanel", description="Deploy panel order member ke channel panel")
async def deployorderpanel(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return

    channel = bot.get_channel(PANEL_CHANNEL_ID)
    if channel is None:
        await interaction.response.send_message("PANEL_CHANNEL_ID tidak valid.", ephemeral=True)
        return

    embed = build_member_order_embed()
    await channel.send(embed=embed, view=MemberOrderPanelView())

    log_activity(
        str(member.id), str(member), actor_role(member),
        "DEPLOY_ORDER_PANEL", "CHANNEL", str(PANEL_CHANNEL_ID),
        "Deploy panel order member"
    )

    await interaction.response.send_message(
        "✅ Panel order member berhasil dikirim ke channel panel.",
        ephemeral=True
    )


@bot.tree.command(name="adminpanel", description="Buka admin panel pribadi")
async def adminpanel(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=discord.Embed(title="Admin Panel", description="Panel admin pribadi", color=discord.Color.red()),
        view=AdminPanelView(),
        ephemeral=True
    )


@bot.tree.command(name="helperpanel", description="Buka helper panel pribadi")
async def helperpanel(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_helper_member(member):
        await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
        return
    await interaction.response.send_message(
        embed=discord.Embed(title="Helper Panel", description="Panel helper pribadi", color=discord.Color.blurple()),
        view=HelperPanelView(),
        ephemeral=True
    )


@bot.tree.command(name="orderpanel", description="Buka panel order member")
async def orderpanel(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=build_member_order_embed(),
        view=MemberOrderPanelView(),
        ephemeral=True
    )


@bot.tree.command(name="dashboard", description="Lihat dashboard statistik")
async def dashboard(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_helper_member(member):
        await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_dashboard_embed(), ephemeral=True)


@bot.tree.command(name="logs", description="Lihat log aktivitas terbaru")
async def logs(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_logs_embed(), ephemeral=True)


@bot.tree.command(name="addproduk", description="Tambah produk")
@app_commands.describe(nama="Nama produk", harga="Harga", stok="Stok", deskripsi="Deskripsi")
async def addproduk(interaction: discord.Interaction, nama: str, harga: int, stok: int, deskripsi: str = ""):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO products (name, price, stock, description)
            VALUES (?, ?, ?, ?)
        """, (nama, harga, stok, deskripsi))
        conn.commit()
        conn.close()

        log_activity(
            str(member.id), str(member), actor_role(member),
            "ADD_PRODUCT", "PRODUCT", nama,
            f"Harga={harga}, Stok={stok}"
        )

        await interaction.response.send_message(f"✅ Produk **{nama}** ditambahkan.", ephemeral=True)
    except sqlite3.IntegrityError:
        await interaction.response.send_message("❌ Nama produk sudah ada.", ephemeral=True)


@bot.tree.command(name="setstok", description="Ubah stok produk")
@app_commands.describe(nama="Nama produk", stok="Stok baru")
async def setstok(interaction: discord.Interaction, nama: str, stok: int):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_admin_member(member):
        await interaction.response.send_message("Tidak punya akses admin.", ephemeral=True)
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE products SET stock = ? WHERE LOWER(name)=LOWER(?)", (stok, nama))
    conn.commit()
    changed = cur.rowcount
    conn.close()

    if changed == 0:
        await interaction.response.send_message("❌ Produk tidak ditemukan.", ephemeral=True)
        return

    log_activity(
        str(member.id), str(member), actor_role(member),
        "SET_STOCK", "PRODUCT", nama,
        f"Stok baru={stok}"
    )

    await interaction.response.send_message(f"✅ Stok **{nama}** jadi **{stok}**.", ephemeral=True)


@bot.tree.command(name="listproduk", description="Lihat daftar produk")
async def listproduk(interaction: discord.Interaction):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name, price, stock, description FROM products ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await interaction.response.send_message("Belum ada produk.", ephemeral=True)
        return

    embed = discord.Embed(title="Daftar Produk", color=discord.Color.blue())
    for name, price, stock, description in rows:
        embed.add_field(
            name=f"{name} | {rupiah(price)}",
            value=f"Stok: **{stock}**\n{description or '-'}",
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stok", description="Cek stok produk")
@app_commands.describe(nama="Nama produk")
async def stok(interaction: discord.Interaction, nama: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name, price, stock, description FROM products WHERE LOWER(name)=LOWER(?)", (nama,))
    row = cur.fetchone()
    conn.close()

    if not row:
        await interaction.response.send_message("❌ Produk tidak ditemukan.", ephemeral=True)
        return

    name, price, stock_value, description = row
    embed = discord.Embed(title=f"Stok Produk: {name}", color=discord.Color.green())
    embed.add_field(name="Harga", value=rupiah(price), inline=True)
    embed.add_field(name="Stok", value=str(stock_value), inline=True)
    embed.add_field(name="Deskripsi", value=description or "-", inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="order", description="Buat invoice otomatis")
@app_commands.describe(nama="Nama produk", jumlah="Jumlah beli")
async def order(interaction: discord.Interaction, nama: str, jumlah: int):
    if jumlah <= 0:
        await interaction.response.send_message("❌ Jumlah harus lebih dari 0.", ephemeral=True)
        return

    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, name, price, stock FROM products WHERE LOWER(name)=LOWER(?)", (nama,))
    product = cur.fetchone()

    if not product:
        conn.close()
        await interaction.response.send_message("❌ Produk tidak ditemukan.", ephemeral=True)
        return

    product_id, product_name, unit_price, stock_value = product

    if stock_value < jumlah:
        conn.close()
        await interaction.response.send_message(
            f"❌ Stok tidak cukup. Stok tersedia: **{stock_value}**",
            ephemeral=True
        )
        return

    invoice_code = generate_invoice_code()
    total_price = unit_price * jumlah
    created_at = now_str()
    due_at = (now_dt() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

    try:
        cur.execute("""
            INSERT INTO invoices (
                invoice_code, user_id, username, product_id, product_name,
                quantity, unit_price, total_price, status, created_at, due_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            invoice_code,
            str(interaction.user.id),
            str(interaction.user),
            product_id,
            product_name,
            jumlah,
            unit_price,
            total_price,
            "UNPAID",
            created_at,
            due_at
        ))
        conn.commit()
        conn.close()

        row = get_invoice_detail(invoice_code)
        embed = build_invoice_embed(row)

        log_activity(
            str(interaction.user.id), str(interaction.user), "USER",
            "CREATE_ORDER", "INVOICE", invoice_code,
            f"{product_name} x{jumlah}"
        )

        await interaction.response.send_message(
            f"✅ Invoice berhasil dibuat: **{invoice_code}**\nCek DM kamu untuk detail invoice.",
            ephemeral=True
        )

        try:
            await interaction.user.send("Berikut invoice pesanan kamu:", embed=embed)
        except Exception:
            await interaction.followup.send(
                "⚠️ Aku tidak bisa kirim DM. Aktifkan DM server ya.",
                ephemeral=True
            )

        await send_admin_log(
            content=f"🧾 Invoice baru dari {interaction.user.mention}",
            embed=embed
        )

    except Exception as e:
        conn.rollback()
        conn.close()
        await interaction.response.send_message(f"❌ Gagal membuat invoice: {e}", ephemeral=True)

@bot.tree.command(name="invoice", description="Lihat detail invoice")
@app_commands.describe(kode="Kode invoice")
async def invoice(interaction: discord.Interaction, kode: str):
    row = get_invoice_detail(kode)
    if not row:
        await interaction.response.send_message("❌ Invoice tidak ditemukan.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_invoice_embed(row), ephemeral=True)


@bot.tree.command(name="pendinginvoice", description="Lihat invoice pending")
async def pendinginvoice(interaction: discord.Interaction):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_helper_member(member):
        await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_pending_embed(), ephemeral=True)


@bot.tree.command(name="bayar", description="Konfirmasi invoice sudah dibayar")
@app_commands.describe(invoice_code="Kode invoice")
async def bayar(interaction: discord.Interaction, invoice_code: str):
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_helper_member(member):
        await interaction.response.send_message("Tidak punya akses helper/admin.", ephemeral=True)
        return

    result = confirm_payment_and_reduce_stock(invoice_code, str(member))
    if not result["ok"]:
        await interaction.response.send_message(f"❌ {result['message']}", ephemeral=True)
        return

    log_activity(
        str(member.id), str(member), actor_role(member),
        "CONFIRM_PAYMENT", "INVOICE", invoice_code,
        f"Produk={result['product_name']}, Qty={result['quantity']}, StokSisa={result['new_stock']}"
    )

    await interaction.response.send_message(
        f"✅ Invoice **{invoice_code}** berhasil dibayar.\n"
        f"Stok baru: **{result['new_stock']}**",
        ephemeral=True
    )

    try:
        user = await bot.fetch_user(int(result["user_id"]))
        embed = discord.Embed(title="Pembayaran Diterima", color=discord.Color.green())
        embed.add_field(name="Invoice", value=invoice_code, inline=False)
        embed.add_field(name="Produk", value=result["product_name"], inline=False)
        embed.add_field(name="Qty", value=str(result["quantity"]), inline=True)
        embed.add_field(name="Total", value=rupiah(result["total_price"]), inline=True)
        embed.add_field(name="Status", value="PAID", inline=True)
        await user.send(embed=embed)
    except Exception:
        pass


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_TOKEN belum diisi di file .env")
    bot.run(TOKEN)
