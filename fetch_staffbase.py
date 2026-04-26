import requests
import json

def get_staffbase_article_urls():
    # 初回のAPIエンドポイント
    url = "https://support.staffbase.com/api/v2/help_center/ja/articles.json"
    article_list = []

    print("Fetching article list...")

    while url:
        response = requests.get(url)
        if response.status_code != 200:
            print(f"Error: {response.status_code}")
            break

        data = response.json()
        articles = data.get('articles', [])

        for article in articles:
            # 記事タイトルとURLをペアで保存
            article_list.append({
                "title": article['title'],
                "url": article['html_url']
            })

        # 次のページがあるか確認
        url = data.get('next_page')
        print(f"Retrieved {len(article_list)} articles so far...")

    return article_list

# 実行
all_articles = get_staffbase_article_urls()

# 結果の表示（またはファイル書き出し）
with open("staffbase_urls.md", "w", encoding="utf-8") as f:
    f.write("# Staffbase Knowledge Base URL List\n\n")
    for item in all_articles:
        f.write(f"- [{item['title']}]({item['url']})\n")

print(f"\nDone! Total articles found: {len(all_articles)}")
print("Saved to staffbase_urls.md")
