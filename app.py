"""
Learn-with-Me: Enhanced Flashcard App

Features:
- Upload PDF, extract text (PyPDF2)
- Generate logical fill-in-the-blank flashcards
- Store in SQLite (flashcards.db)
- View & search flashcards with multi-delete option
- Quiz with manual difficulty selection
- Read/Unread status tracking
- Proper numbering after deletion
- Automatic database migration

Save this file as `app.py` and run: `streamlit run app.py`
"""

import streamlit as st
import PyPDF2
import sqlite3
import random
import os
import re
from datetime import date, datetime, timedelta

# -------------------------------
# Configuration
# -------------------------------
DB_PATH = "flashcards.db"

# -------------------------------
# Database helpers with Migration
# -------------------------------

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Create main table
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS flashcards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            difficulty INTEGER DEFAULT 1,
            next_review TEXT
        )
        """
    )
    
    # Check if new columns exist and add them if not
    c.execute("PRAGMA table_info(flashcards)")
    columns = [col[1] for col in c.fetchall()]
    
    if 'is_read' not in columns:
        c.execute("ALTER TABLE flashcards ADD COLUMN is_read BOOLEAN DEFAULT FALSE")
    
    if 'display_order' not in columns:
        c.execute("ALTER TABLE flashcards ADD COLUMN display_order INTEGER")
        # Initialize display_order for existing records
        c.execute("SELECT id FROM flashcards ORDER BY id")
        existing_ids = [row[0] for row in c.fetchall()]
        for order, card_id in enumerate(existing_ids, 1):
            c.execute("UPDATE flashcards SET display_order = ? WHERE id = ?", (order, card_id))
    
    conn.commit()
    conn.close()


def insert_flashcard(question, answer, difficulty=1, next_review=None):
    if next_review is None:
        next_review = date.today().isoformat()
    
    # Get the next display order number
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(display_order), 0) FROM flashcards")
    next_order = c.fetchone()[0] + 1
    
    c.execute(
        "INSERT INTO flashcards (question, answer, difficulty, next_review, is_read, display_order) VALUES (?, ?, ?, ?, ?, ?)",
        (question, answer, difficulty, next_review, False, next_order),
    )
    conn.commit()
    conn.close()


def update_flashcard_review(card_id, new_difficulty, next_review_date):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE flashcards SET difficulty = ?, next_review = ? WHERE id = ?",
        (new_difficulty, next_review_date.isoformat(), card_id),
    )
    conn.commit()
    conn.close()


def mark_as_read(card_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "UPDATE flashcards SET is_read = TRUE WHERE id = ?",
        (card_id,),
    )
    conn.commit()
    conn.close()


def delete_flashcard(card_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM flashcards WHERE id = ?", (card_id,))
    conn.commit()
    conn.close()
    # Reorder remaining flashcards
    reorder_flashcards()


def delete_multiple_flashcards(card_ids):
    if not card_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ','.join('?' for _ in card_ids)
    c.execute(f"DELETE FROM flashcards WHERE id IN ({placeholders})", card_ids)
    conn.commit()
    conn.close()
    # Reorder remaining flashcards
    reorder_flashcards()


def reorder_flashcards():
    """Renumber display_order after deletions to maintain proper numbering"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Get all flashcards ordered by their current display_order
    c.execute("SELECT id FROM flashcards ORDER BY display_order")
    flashcards = c.fetchall()
    
    # Update display_order sequentially
    for new_order, (card_id,) in enumerate(flashcards, 1):
        c.execute("UPDATE flashcards SET display_order = ? WHERE id = ?", (new_order, card_id))
    
    conn.commit()
    conn.close()

# -------------------------------
# PDF extraction & QA generation
# -------------------------------

def extract_text_from_pdf(uploaded_file):
    try:
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
    except Exception as e:
        st.error(f"Failed to read PDF: {e}")
        return ""

    text = ""
    total_pages = len(pdf_reader.pages) if getattr(pdf_reader, 'pages', None) else 0
    progress = st.progress(0)

    for i, page in enumerate(pdf_reader.pages):
        try:
            page_text = page.extract_text()
        except Exception:
            page_text = None
        if page_text:
            text += page_text + "\n"
        if total_pages:
            progress.progress((i + 1) / total_pages)
    progress.empty()
    return text


def clean_text(text):
    return " ".join(text.split())


def generate_logical_qa_pairs(text, num_questions=5):
    """Generate more logical and meaningful flashcards"""
    # Split into sentences
    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 50]
    
    questions = []
    
    for i in range(min(num_questions, len(sentences))):
        sent = sentences[i]
        words = sent.split()
        
        if len(words) < 8:
            continue
            
        # Try to find important nouns, verbs, or adjectives
        important_words = []
        for j, word in enumerate(words):
            clean_word = word.strip('.,!?;:"()[]{}')
            # Skip short words, articles, prepositions
            if (len(clean_word) > 4 and 
                not clean_word.lower() in ['the', 'and', 'for', 'with', 'that', 'this', 'which', 'from', 'have', 'has', 'had'] and
                j > 2 and j < len(words) - 2):
                important_words.append((j, clean_word))
        
        if not important_words:
            continue
            
        # Choose the most important word (longest or in middle)
        important_words.sort(key=lambda x: (-len(x[1]), abs(x[0] - len(words)//2)))
        idx, blank_word = important_words[0]
        
        # Create question with blank
        question_text = sent.replace(words[idx], "_____", 1)
        
        questions.append({
            "question": question_text,
            "answer": blank_word,
            "sentence": sent
        })
    
    return questions

# -------------------------------
# Quiz Functions
# -------------------------------

def run_quiz():
    """Run the quiz based on configuration"""
    config = st.session_state.quiz_config
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Build query based on configuration
    query = "SELECT id, question, answer, difficulty, next_review FROM flashcards"
    conditions = []
    params = []
    
    # Difficulty filter
    if config['difficulty'] == "Easy (1-2)":
        conditions.append("difficulty BETWEEN 1 AND 2")
    elif config['difficulty'] == "Medium (3)":
        conditions.append("difficulty = 3")
    elif config['difficulty'] == "Hard (4-5)":
        conditions.append("difficulty BETWEEN 4 AND 5")
    
    # Quiz type filter
    if config['type'] == "Due Cards":
        conditions.append("(next_review <= ? OR next_review IS NULL)")
        params.append(date.today().isoformat())
    
    # Build final query
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    
    query += " ORDER BY RANDOM() LIMIT ?"
    params.append(config['num_questions'])
    
    c.execute(query, params)
    quiz_cards = c.fetchall()
    conn.close()
    
    return quiz_cards

# -------------------------------
# Main Application
# -------------------------------

def main():
    st.set_page_config(page_title="Learn-with-Me", page_icon="🎓", layout="wide")
    init_db()  # This will now handle database migration

    # Initialize session state variables
    if 'selected_cards' not in st.session_state:
        st.session_state.selected_cards = set()
    if 'quiz_started' not in st.session_state:
        st.session_state.quiz_started = False
    if 'quiz_cards' not in st.session_state:
        st.session_state.quiz_cards = []
    if 'quiz_options' not in st.session_state:
        st.session_state.quiz_options = {}

    # Sidebar navigation
    with st.sidebar:
        st.markdown("# 🎓 Learn-with-Me")
        st.markdown("---")
        
        # Page selection
        page = st.radio(
            "**Navigation**",
            ["📁 Upload Notes", "📚 View Flashcards", "🧠 Quiz", "📊 Performance"],
            index=0
        )
        
        st.markdown("---")
        st.markdown("### 📊 Statistics")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM flashcards")
        total_cards = c.fetchone()[0]
        
        # Safe query for is_read column
        try:
            c.execute("SELECT COUNT(*) FROM flashcards WHERE is_read = FALSE")
            unread_cards = c.fetchone()[0]
        except:
            unread_cards = total_cards  # If column doesn't exist yet, assume all are unread
        
        today_iso = date.today().isoformat()
        c.execute("SELECT COUNT(*) FROM flashcards WHERE next_review <= ? OR next_review IS NULL", (today_iso,))
        due_cards = c.fetchone()[0]
        
        # Get difficulty distribution
        c.execute("SELECT difficulty, COUNT(*) FROM flashcards GROUP BY difficulty")
        difficulty_stats = dict(c.fetchall())
        conn.close()
        
        st.metric("Total Flashcards", total_cards)
        st.metric("Unread Cards", unread_cards)
        st.metric("Due Today", due_cards)
        
        if total_cards > 0:
            st.markdown("**Difficulty Distribution:**")
            for diff in range(1, 6):
                count = difficulty_stats.get(diff, 0)
                if count > 0:
                    percentage = (count / total_cards) * 100
                    st.write(f"Level {diff}: {count} ({percentage:.1f}%)")
        
        st.markdown("---")
        st.markdown("✨ Smart Flashcards")
        st.markdown("📚 Logical Learning")

    # Main content
    st.markdown("# 📚 Enhanced Learn-with-Me Flashcards")

    # --- Upload Notes ---
    if page == "📁 Upload Notes":
        st.header("📁 Upload Your Study Materials")
        
        col1, col2 = st.columns(2)
        with col1:
            uploaded_file = st.file_uploader("Choose a PDF file", type=["pdf"]) 
        with col2:
            num_q = st.number_input("Number of flashcards to generate", min_value=1, max_value=30, value=8)

        if uploaded_file is not None:
            text = extract_text_from_pdf(uploaded_file)
            cleaned = clean_text(text)

            with st.expander("📄 View Extracted Text"):
                st.text_area("Content", cleaned, height=200)

            if st.button("Generate Logical Flashcards", key="gen_cards", type="primary"):
                with st.spinner("Generating smart flashcards..."):
                    qa_pairs = generate_logical_qa_pairs(cleaned, num_questions=int(num_q))
                
                if not qa_pairs:
                    st.warning("Couldn't generate quality flashcards. Try a different file with more substantive content.")
                else:
                    valid_count = 0
                    for pair in qa_pairs:
                        if (pair["question"].strip() and pair["answer"].strip()):
                            insert_flashcard(
                                pair["question"], 
                                pair["answer"], 
                                difficulty=1, 
                                next_review=date.today().isoformat()
                            )
                            valid_count += 1
                    
                    if valid_count > 0:
                        st.success(f"✅ Generated {valid_count} logical flashcards!")
                        st.balloons()
                        
                        # Show sample of generated flashcards
                        with st.expander("View Sample Flashcards"):
                            for i, pair in enumerate(qa_pairs[:3]):
                                st.write(f"**Q{i+1}:** {pair['question']}")
                                st.write(f"**A{i+1}:** {pair['answer']}")
                                st.write(f"*Original:* {pair['sentence']}")
                                st.markdown("---")
                    else:
                        st.warning("No valid flashcards were generated.")

    # --- View Flashcards ---
    elif page == "📚 View Flashcards":
        st.header("📚 Your Flashcard Collection")
        
        search_term = st.text_input("🔍 Search flashcards by keyword:")

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Safe query with fallback for display_order
        try:
            if search_term:
                query = """SELECT id, question, answer, difficulty, next_review, is_read, display_order 
                         FROM flashcards 
                         WHERE question LIKE ? OR answer LIKE ? 
                         ORDER BY display_order"""
                params = (f"%{search_term}%", f"%{search_term}%")
            else:
                query = "SELECT id, question, answer, difficulty, next_review, is_read, display_order FROM flashcards ORDER BY display_order"
                params = ()
        except:
            # Fallback if display_order doesn't exist yet
            if search_term:
                query = """SELECT id, question, answer, difficulty, next_review, 0 as is_read, id as display_order 
                         FROM flashcards 
                         WHERE question LIKE ? OR answer LIKE ? 
                         ORDER BY id"""
                params = (f"%{search_term}%", f"%{search_term}%")
            else:
                query = "SELECT id, question, answer, difficulty, next_review, 0 as is_read, id as display_order FROM flashcards ORDER BY id"
                params = ()
        
        c.execute(query, params)
        rows = c.fetchall()
        conn.close()

        if not rows:
            st.info("No flashcards found. Upload notes to create flashcards.")
        else:
            st.write(f"**Found {len(rows)} flashcards**")
            
            # Multi-select and delete
            col1, col2 = st.columns([4, 1])
            with col2:
                if st.button("🗑️ Delete Selected", type="secondary", disabled=not st.session_state.selected_cards):
                    delete_multiple_flashcards(list(st.session_state.selected_cards))
                    st.success(f"Deleted {len(st.session_state.selected_cards)} flashcards!")
                    st.session_state.selected_cards = set()
                    st.rerun()
            
            for r in rows:
                card_id, question, answer, difficulty, next_review, is_read, display_order = r
                
                col1, col2 = st.columns([1, 20])
                with col1:
                    selected = st.checkbox(
                        "", 
                        key=f"select_{card_id}", 
                        value=card_id in st.session_state.selected_cards
                    )
                    if selected:
                        st.session_state.selected_cards.add(card_id)
                    elif card_id in st.session_state.selected_cards:
                        st.session_state.selected_cards.remove(card_id)
                
                with col2:
                    # Check if we can mark as read (column exists)
                    can_mark_read = True
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("PRAGMA table_info(flashcards)")
                        columns = [col[1] for col in c.fetchall()]
                        can_mark_read = 'is_read' in columns
                        conn.close()
                    except:
                        can_mark_read = False
                    
                    # Mark as read when expanded
                    with st.expander(f"#{display_order} - {'✅' if is_read and can_mark_read else '❌'}: {question[:40]}...", expanded=False):
                        # Mark as read when user opens the flashcard (if possible)
                        if can_mark_read and not is_read:
                            mark_as_read(card_id)
                            st.rerun()
                        
                        st.write(f"**Q:** {question}")
                        st.write(f"**A:** {answer}")
                        st.write(f"**Difficulty:** {difficulty}")
                        if can_mark_read:
                            st.write(f"**Status:** {'Read' if is_read else 'Unread'}")
                        st.write(f"**Next review:** {next_review}")
                        
                        if st.button("Delete This", key=f"delete_single_{card_id}"):
                            delete_flashcard(card_id)
                            st.success(f"Flashcard #{display_order} deleted!")
                            st.rerun()

    # --- Quiz ---
    elif page == "🧠 Quiz":
        st.header("🧠 Smart Quiz Mode")
    
        if not st.session_state.quiz_started:
            # Quiz configuration
            col1, col2 = st.columns(2)
            with col1:
                difficulty_level = st.selectbox(
                    "Quiz Difficulty",
                    ["All Levels", "Easy (1-2)", "Medium (3)", "Hard (4-5)"],
                    index=0
                )
            with col2:
                quiz_type = st.selectbox(
                    "Quiz Type",
                    ["Due Cards", "All Cards"],
                    index=0
                )

            num_questions = st.slider("Number of Questions", 5, 50, 15)

            # Start quiz button
            if st.button("Start Smart Quiz", type="primary"):
                st.session_state.quiz_config = {
                    'difficulty': difficulty_level,
                    'type': quiz_type,
                    'num_questions': num_questions
                }
                st.session_state.quiz_cards = run_quiz()
            
                if not st.session_state.quiz_cards:
                    st.error("No flashcards match your quiz criteria. Try different settings.")
                else:
                    st.session_state.quiz_started = True
                    st.session_state.quiz_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = {}
                    st.session_state.user_choices = {}
                    st.session_state.quiz_options = {}
                    st.rerun()
        else:
            # Quiz in progress
            if st.session_state.quiz_index < len(st.session_state.quiz_cards):
                card = st.session_state.quiz_cards[st.session_state.quiz_index]
                card_id, question, answer, difficulty, next_review = card

                st.subheader(f"Question {st.session_state.quiz_index + 1} of {len(st.session_state.quiz_cards)}")
                st.markdown(f"**{question}**")

                # Generate options for this question
                if st.session_state.quiz_index not in st.session_state.quiz_options:
                    all_answers = [row[2] for row in st.session_state.quiz_cards if row[0] != card_id and row[2].strip() != ""]
                    unique_answers = list(set(all_answers))
                    distractor_pool = [ans for ans in unique_answers if ans != answer]
                    sample_count = min(3, len(distractor_pool))
                    distractors = random.sample(distractor_pool, sample_count) if sample_count > 0 else []
                    options = distractors + [answer]
                    options = list(set(options))
                    generic_options = ["All of the above", "None of the above", "Not sure"]
                    for option in generic_options:
                        if len(options) < 4 and option not in options:
                            options.append(option)
                    random.shuffle(options)
                    st.session_state.quiz_options[st.session_state.quiz_index] = options

                # Display options
                user_choice = st.radio(
                    "Choose the correct answer:", 
                    st.session_state.quiz_options[st.session_state.quiz_index], 
                    key=f"mcq_{st.session_state.quiz_index}"
                )
                st.session_state.user_choices[st.session_state.quiz_index] = user_choice

                # Navigation
                col1, col2 = st.columns(2)
                with col1:
                    if st.session_state.quiz_index > 0 and st.button("← Previous"):
                        st.session_state.quiz_index -= 1
                        st.rerun()
                with col2:
                    if st.button("Next →"):
                        if st.session_state.quiz_index < len(st.session_state.quiz_cards) - 1:
                            st.session_state.quiz_index += 1
                            st.rerun()
                        else:
                            # Calculate score at the end
                            for i, card in enumerate(st.session_state.quiz_cards):
                                if st.session_state.user_choices.get(i) == card[2]:
                                    st.session_state.score += 1
                            st.session_state.quiz_index += 1
                            st.rerun()

            else:
                # Quiz completed - Show results
                st.success("🎉 Quiz Completed!")
                accuracy = (st.session_state.score / len(st.session_state.quiz_cards)) * 100
            
                col1, col2 = st.columns(2)
                with col1:
                    st.metric("Score", f"{st.session_state.score}/{len(st.session_state.quiz_cards)}")
                with col2:
                    st.metric("Accuracy", f"{accuracy:.1f}%")
            
                if accuracy >= 80:
                    st.success("Excellent! 🎯")
                elif accuracy >= 60:
                    st.info("Good job! 👍")
                else:
                    st.warning("Keep practicing! 📚")
            
                # Show detailed question review
                st.markdown("---")
                st.subheader("📝 Question Review")
            
                for i, card in enumerate(st.session_state.quiz_cards):
                    card_id, question, correct_answer, difficulty, next_review = card
                    user_answer = st.session_state.user_choices.get(i, "Not answered")
                
                    # Create expander for each question
                    with st.expander(f"Question {i+1}: {question[:50]}...", expanded=False):
                        col1, col2 = st.columns(2)
                    
                        with col1:
                            st.markdown("**Your Answer:**")
                            if user_answer == correct_answer:
                                st.success(f"✅ {user_answer}")
                            else:
                                st.error(f"❌ {user_answer}")
                    
                        with col2:
                            st.markdown("**Correct Answer:**")
                            st.info(f"📗 {correct_answer}")
                    
                        # Show explanation
                        st.markdown("**Question:**")
                        st.write(question)
                    
                        # Show difficulty
                        st.markdown("**Difficulty Level:**")
                        st.write(f"Level {difficulty}")
                    
                        st.markdown("---")
            
                # Restart quiz button
                if st.button("Restart Quiz"):
                    st.session_state.quiz_started = False
                    st.session_state.quiz_index = 0
                    st.session_state.score = 0
                    st.session_state.answered = {}
                    st.session_state.user_choices = {}
                    st.session_state.quiz_options = {}
                    st.rerun()
    # --- Performance ---
    elif page == "📊 Performance":
        st.header("📊 Performance Dashboard")
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()

        # Get stats
        c.execute("SELECT COUNT(*) FROM flashcards")
        total = c.fetchone()[0]
        
        # Safe query for is_read column
        try:
            c.execute("SELECT COUNT(*) FROM flashcards WHERE is_read = FALSE")
            unread = c.fetchone()[0]
        except:
            unread = total  # If column doesn't exist yet, assume all are unread
        
        c.execute("SELECT difficulty, COUNT(*) FROM flashcards GROUP BY difficulty ORDER BY difficulty")
        difficulty_stats = c.fetchall()
        
        today_iso = date.today().isoformat()
        c.execute("SELECT COUNT(*) FROM flashcards WHERE next_review <= ? OR next_review IS NULL", (today_iso,))
        due_count = c.fetchone()[0]
        
        conn.close()

        if total == 0:
            st.info("No flashcards yet. Upload some notes to get started!")
        else:
            # Overview metrics
            st.subheader("📈 Overview")
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total Flashcards", total)
            with col2:
                st.metric("Unread Cards", unread)
            with col3:
                st.metric("Due Today", due_count)
            with col4:
                read_count = total - unread
                completion = (read_count / total) * 100 if total > 0 else 0
                st.metric("Read", f"{completion:.1f}%")

            # Difficulty chart
            st.subheader("🎯 Difficulty Distribution")
            for d, cnt in difficulty_stats:
                percentage = (cnt / total) * 100
                st.write(f"**Level {d}:** {cnt} cards")
                st.progress(percentage / 100, text=f"{percentage:.1f}%")

            # Recommendations
            st.subheader("💡 Recommendations")
            if due_count == 0 and unread == 0:
                st.success("✅ You're all caught up! Great job!")
            elif unread > 0:
                st.info(f"📖 You have {unread} unread cards. Time to explore new material!")
            elif due_count > 0:
                st.warning(f"⏰ You have {due_count} cards due for review. Time to study!")


if __name__ == "__main__":
    main()