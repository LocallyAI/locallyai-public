# Welcome to LocallyAI / مرحباً بك في LocallyAI

> **Bilingual welcome document.** Synthetic demo content. Used to
> verify that the Saudi deployment ingests + retrieves Arabic
> alongside English. The same paragraph is provided in both languages
> on the same page; users testing the deployment can ask in either
> language and confirm the model finds + cites this document.

---

## EN — What LocallyAI does

LocallyAI is an on-premises AI assistant for legal and professional
teams. It runs entirely on your firm's hardware: the LLM, the
document index, the OpenAI-compatible API, and the audit log. **No
data leaves the deployment.** No outbound API calls are made during
operation. The deployment can be air-gapped after install.

The system uses Retrieval-Augmented Generation (RAG): when you ask a
question, the system finds the most relevant passages from the
documents in your firm's corpus, gives them to the language model
together with your question, and the model produces an answer
citing the source documents.

Privacy posture (Saudi Arabia deployment):
- Pseudonymised user identifiers in every audit-log entry (PDPL
  Art. 19).
- Tamper-evident HMAC-chained audit log (ISO 27001 A.8.15).
- BitLocker / FileVault disk encryption (mandatory; PDPL Art. 19).
- Salt rotation supported via `manage_users.py rotate-audit-salt`.
- Records of Processing Activities at `/admin/processing-record`
  (RoPA v1.3 — stamps `data_region: "KSA"`).

For more, see the firm's data processing policy and the LocallyAI
Standard Operating Procedure document.

---

## AR — ماذا يفعل LocallyAI

LocallyAI هو مساعد ذكاء اصطناعي محلي مخصص للفرق القانونية والمهنية.
يعمل بالكامل على عتاد مكتبك: نموذج اللغة الكبير، فهرس الوثائق،
واجهة OpenAI المتوافقة، وسجل التدقيق. **لا تغادر أي بيانات الجهاز
الذي تم تثبيت النظام عليه.** لا تُجرى أي مكالمات شبكية خارجية أثناء
التشغيل. يمكن عزل الجهاز عن الإنترنت بعد التثبيت.

يستخدم النظام التوليد المعزز بالاسترجاع (RAG): عندما تطرح سؤالاً،
يبحث النظام عن أكثر المقاطع صلة في وثائق مؤسستك، ويمررها إلى نموذج
اللغة مع سؤالك، فيُنتج النموذج إجابةً مع الإشارة إلى الوثائق المصدر.

الموقف من الخصوصية (نشر المملكة العربية السعودية):
- معرّفات المستخدمين مُعلَّمة بأسماء مستعارة في كل سجل تدقيق
  (المادة 19 من نظام حماية البيانات الشخصية).
- سجل تدقيق محصَّن ضد العبث (سلسلة HMAC؛ ISO 27001 A.8.15).
- تشفير القرص بـ BitLocker / FileVault (إلزامي).
- دوران الملح المشفّر مدعوم عبر `manage_users.py rotate-audit-salt`.
- سجل أنشطة المعالجة عبر `/admin/processing-record`
  (الإصدار 1.3 — يحمل `data_region: "KSA"`).

للمزيد، راجع سياسة معالجة البيانات الشخصية للمؤسسة ووثيقة إجراءات
التشغيل القياسية الخاصة بـ LocallyAI.

---

## Test queries / استعلامات اختبار

Try (English): "What does LocallyAI do? Cite the welcome document."

جرب (عربي): «ماذا يفعل LocallyAI؟ استشهد بوثيقة الترحيب.»

Both queries should retrieve **this** document and produce an answer
in the same language as the query.
