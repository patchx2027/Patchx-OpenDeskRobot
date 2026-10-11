package com.patchx_main.ability.aiui.bean;

public class StoryBean {
    private String author;
    private String category;
    private String id;
    private String name;
    private String playUrl;
    private String resourceId;
    private String series;
    private String source;
    private String status;

    public void setAuthor(String author) {
        this.author = author;
    }

    public String getAuthor() {
        return author;
    }

    public void setCategory(String category) {
        this.category = category;
    }

    public String getCategory() {
        return category;
    }

    public void setId(String id) {
        this.id = id;
    }

    public String getId() {
        return id;
    }

    public void setName(String name) {
        this.name = name;
    }

    public String getName() {
        return name;
    }

    public void setPlayUrl(String playUrl) {
        this.playUrl = playUrl;
    }

    public String getPlayUrl() {
        return playUrl;
    }

    public void setResourceId(String resourceId) {
        this.resourceId = resourceId;
    }

    public String getResourceId() {
        return resourceId;
    }

    public void setSeries(String series) {
        this.series = series;
    }

    public String getSeries() {
        return series;
    }

    public void setSource(String source) {
        this.source = source;
    }

    public String getSource() {
        return source;
    }

    public void setStatus(String status) {
        this.status = status;
    }

    public String getStatus() {
        return status;
    }

    @Override
    public String toString() {
        return "StoryBean{" +
                "author='" + author + '\'' +
                ", category='" + category + '\'' +
                ", id='" + id + '\'' +
                ", name='" + name + '\'' +
                ", playUrl='" + playUrl + '\'' +
                ", resourceId='" + resourceId + '\'' +
                ", series='" + series + '\'' +
                ", source='" + source + '\'' +
                ", status='" + status + '\'' +
                '}';
    }
}
